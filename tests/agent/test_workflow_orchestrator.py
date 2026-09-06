import time

from src.agent.contracts import AgentContext, AgentErrorCode, ToolResult
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep


class FakeTool:
    def __init__(self, name, success=True):
        self.name = name
        self.success = success
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        if self.success:
            return {
                "success": True,
                "message": f"{self.name} ok",
                "data": {"input": query},
                "formatted": f"{self.name}: {query}",
            }
        return {
            "success": False,
            "message": f"{self.name} failed",
        }


def test_workflow_runs_tools_in_order():
    context = AgentContext(query="analyze CCO", trace_id="test-trace")
    workflow = WorkflowOrchestrator()
    tools = {
        "property_calculator": FakeTool("property_calculator"),
        "admet_predictor": FakeTool("admet_predictor"),
    }

    result = workflow.run(
        context=context,
        steps=[
            WorkflowStep("properties", "property_calculator", "CCO"),
            WorkflowStep("admet", "admet_predictor", "CCO"),
        ],
        tools=tools,
    )

    assert result.success is True
    assert [item.tool_name for item in result.tool_results] == [
        "property_calculator",
        "admet_predictor",
    ]
    assert tools["property_calculator"].calls == ["CCO"]
    assert tools["admet_predictor"].calls == ["CCO"]


def test_workflow_preserves_partial_results_when_a_step_fails():
    context = AgentContext(query="analyze CCO", trace_id="test-trace")
    workflow = WorkflowOrchestrator()

    result = workflow.run(
        context=context,
        steps=[
            WorkflowStep("properties", "property_calculator", "CCO"),
            WorkflowStep("admet", "admet_predictor", "CCO"),
        ],
        tools={
            "property_calculator": FakeTool("property_calculator"),
            "admet_predictor": FakeTool("admet_predictor", success=False),
        },
        continue_on_error=True,
    )

    assert result.success is False
    assert result.partial is True
    assert len(result.tool_results) == 2
    assert result.tool_results[0].success is True
    assert result.tool_results[1].success is False


def test_workflow_reports_missing_tool():
    context = AgentContext(query="analyze CCO", trace_id="test-trace")
    workflow = WorkflowOrchestrator()

    result = workflow.run(
        context=context,
        steps=[WorkflowStep("properties", "missing_tool", "CCO")],
        tools={},
    )

    assert result.success is False
    assert result.tool_results[0].error.code == AgentErrorCode.INTERNAL_ERROR


def test_workflow_accepts_standard_tool_result():
    class StandardTool:
        name = "standard"

        def execute(self, query):
            return ToolResult.success_result(self.name, {"query": query}, "ok")

    context = AgentContext(query="analyze CCO", trace_id="test-trace")
    workflow = WorkflowOrchestrator()

    result = workflow.run(
        context=context,
        steps=[WorkflowStep("standard", "standard", "CCO")],
        tools={"standard": StandardTool()},
    )

    assert result.success is True
    assert result.tool_results[0].data == {"query": "CCO"}


def test_workflow_attaches_step_identity_to_each_tool_result():
    result = WorkflowOrchestrator().run(
        context=AgentContext(query="CCO", trace_id="step-identity"),
        steps=[
            WorkflowStep(
                "baseline_properties",
                "property_calculator",
                "CCO",
                output_key="baseline",
            ),
            WorkflowStep(
                "candidate_properties",
                "property_calculator",
                "CCN",
                output_key="candidates",
            ),
        ],
        tools={"property_calculator": FakeTool("property_calculator")},
    )

    assert [item.quality["step_id"] for item in result.tool_results] == [
        "baseline_properties",
        "candidate_properties",
    ]
    assert [item.quality["output_key"] for item in result.tool_results] == [
        "baseline",
        "candidates",
    ]


def test_workflow_passes_generated_smiles_to_downstream_tools():
    class GeneratorTool:
        name = "llm_molecular_generator"

        def execute(self, query):
            return {
                "success": True,
                "message": "generated",
                "data": [{"smiles": "CCO"}, {"smiles": "CCN"}],
                "formatted": "generated molecules",
            }

    context = AgentContext(query="design molecules for EGFR", trace_id="test-trace")
    property_tool = FakeTool("property_calculator")
    admet_tool = FakeTool("admet_predictor")

    result = WorkflowOrchestrator().run(
        context=context,
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                context.query,
                output_key="molecules",
            ),
            WorkflowStep(
                "properties",
                "property_calculator",
                input_from="molecules",
            ),
            WorkflowStep(
                "admet",
                "admet_predictor",
                input_from="molecules",
            ),
        ],
        tools={
            "llm_molecular_generator": GeneratorTool(),
            "property_calculator": property_tool,
            "admet_predictor": admet_tool,
        },
    )

    assert result.success is True
    assert property_tool.calls == ["CCO\nCCN"]
    assert admet_tool.calls == ["CCO\nCCN"]


def test_workflow_can_pass_raw_upstream_output_to_downstream_tool():
    class ReverseTargetTool:
        name = "reverse_target_predictor"

        def execute(self, query):
            return {
                "success": True,
                "message": "targets",
                "data": [{"gene_symbol": "EGFR", "final_similarity": 0.91}],
            }

    target_tool = FakeTool("target_database_search")

    result = WorkflowOrchestrator().run(
        context=AgentContext(query="analyze molecule", trace_id="raw-targets"),
        steps=[
            WorkflowStep(
                "reverse_target",
                "reverse_target_predictor",
                "CCO",
                output_key="targets",
            ),
            WorkflowStep(
                "target_structures",
                "target_database_search",
                input_from="targets",
                metadata={"input_mode": "raw"},
            ),
        ],
        tools={
            "reverse_target_predictor": ReverseTargetTool(),
            "target_database_search": target_tool,
        },
    )

    assert result.success is True
    assert target_tool.calls == [[{"gene_symbol": "EGFR", "final_similarity": 0.91}]]


def test_legacy_input_from_is_resolved_without_unrelated_output_leakage():
    selected = {
        "data": [{"smiles": "CCO"}],
        "source_record_id": "LOCAL_MOLECULES_1",
    }
    outputs = {
        "molecules": selected,
        "unrelated": {"password": "must-not-leak"},
    }
    context = AgentContext(query="analyze", trace_id="legacy-binding")

    raw = WorkflowOrchestrator._resolve_semantic_input(
        context,
        WorkflowStep(
            "raw",
            "property_calculator",
            input_from="molecules",
            input_transform="identity",
            metadata={"input_mode": "raw"},
        ),
        outputs,
    )
    text = WorkflowOrchestrator._resolve_semantic_input(
        context,
        WorkflowStep(
            "text",
            "property_calculator",
            input_from="molecules",
            input_transform="identity",
        ),
        outputs,
    )

    assert raw is selected
    assert text == "CCO"
    assert "must-not-leak" not in repr(raw)
    assert "must-not-leak" not in text


def test_bound_generation_keeps_canonical_payload_instead_of_template_text():
    class ValidGenerator(FakeTool):
        def execute(self, query):
            self.calls.append(query)
            return {
                "success": True,
                "message": "generated",
                "data": [{"smiles": "CCO"}],
            }

    target_tool = FakeTool("target_database_search")
    generator = ValidGenerator("llm_molecular_generator")

    result = WorkflowOrchestrator().run(
        context=AgentContext(
            query="Design molecules for EGFR",
            trace_id="bound-target",
            mol_count=7,
            metadata={"requested_count": 7},
        ),
        steps=[
            WorkflowStep(
                "target_search",
                "target_database_search",
                "EGFR",
                output_key="target",
            ),
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_binding="$.outputs.target",
                input_template=(
                    "User request: {query}\n"
                    "Validated target evidence: {input}"
                ),
            ),
        ],
        tools={
            "target_database_search": target_tool,
            "llm_molecular_generator": generator,
        },
    )

    assert result.partial or result.success
    assert generator.calls == [
        {
            "query": "Design molecules for EGFR",
            "metadata": {"requested_count": 7},
            "outputs": {"target": {"input": "EGFR"}},
        }
    ]


def test_legacy_input_from_generation_keeps_selected_upstream_data_typed():
    target_tool = FakeTool("target_database_search")
    generator = FakeTool("llm_molecular_generator")

    result = WorkflowOrchestrator().run(
        context=AgentContext(
            query="Design molecules for PDE5A",
            trace_id="legacy-bound-target",
            mol_count=7,
            metadata={"requested_count": 7},
        ),
        steps=[
            WorkflowStep(
                "target_search",
                "target_database_search",
                "PDE5A",
                output_key="target",
            ),
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_from="target",
                input_template="{query}: {input}",
                capability="molecule.generate",
            ),
        ],
        tools={
            "target_database_search": target_tool,
            "llm_molecular_generator": generator,
        },
    )

    assert result.partial or result.success
    assert generator.calls == [
        {
            "query": "Design molecules for PDE5A",
            "metadata": {"requested_count": 7},
            "outputs": {"target": {"input": "PDE5A"}},
        }
    ]


def test_required_failure_stops_even_when_workflow_default_continues():
    context = AgentContext(query="analyze CCO", trace_id="required-stop")
    first = FakeTool("required_tool", success=False)
    second = FakeTool("must_not_run")

    result = WorkflowOrchestrator().run(
        context=context,
        steps=[
            WorkflowStep(
                "required",
                "required_tool",
                "CCO",
                required=True,
            ),
            WorkflowStep("after_required", "must_not_run", "CCO"),
        ],
        tools={"required_tool": first, "must_not_run": second},
        continue_on_error=True,
    )

    assert result.success is False
    assert result.partial is False
    assert second.calls == []
    assert result.metadata["workflow_state"]["errors"][0]["step"] == "required"


def test_optional_failure_preserves_state_and_continues():
    context = AgentContext(query="analyze CCO", trace_id="optional-continue")
    optional = FakeTool("optional_tool", success=False)
    final = FakeTool("final_tool")

    result = WorkflowOrchestrator().run(
        context=context,
        steps=[
            WorkflowStep(
                "optional",
                "optional_tool",
                "CCO",
                required=False,
            ),
            WorkflowStep(
                "final",
                "final_tool",
                "CCO",
                output_key="final",
            ),
        ],
        tools={"optional_tool": optional, "final_tool": final},
    )

    assert result.partial is True
    assert final.calls == ["CCO"]
    assert result.metadata["workflow_state"]["outputs"]["final"] == {"input": "CCO"}
    assert result.metadata["workflow_state"]["errors"][0]["step"] == "optional"
    assert any(
        "optional" in warning and "optional_tool" in warning
        for warning in result.warnings
    )


def test_step_timeout_returns_tool_timeout_and_stops_required_workflow():
    class SlowTool:
        name = "slow"

        def execute(self, query):
            time.sleep(0.1)
            return {"success": True, "message": "late", "data": query}

    result = WorkflowOrchestrator().run(
        context=AgentContext(query="CCO", trace_id="timeout"),
        steps=[
            WorkflowStep(
                "slow",
                "slow",
                "CCO",
                timeout_seconds=0.01,
            )
        ],
        tools={"slow": SlowTool()},
    )

    assert result.success is False
    assert result.tool_results[0].error.code == AgentErrorCode.TOOL_TIMEOUT


def test_workflow_state_redacts_sensitive_context_metadata():
    result = WorkflowOrchestrator().run(
        context=AgentContext(
            query="CCO",
            trace_id="redacted-state",
            metadata={"api_key": "secret-value", "request_id": "safe"},
        ),
        steps=[WorkflowStep("properties", "property_calculator", "CCO")],
        tools={"property_calculator": FakeTool("property_calculator")},
    )

    state = result.metadata["workflow_state"]
    assert state["inputs"]["api_key"] == "[REDACTED]"
    assert state["inputs"]["request_id"] == "safe"
