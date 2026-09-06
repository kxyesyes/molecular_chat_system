from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from threading import Barrier, Lock
from typing import get_type_hints

import pytest

from src.agent.contracts import AgentContext, AgentErrorCode, AgentResult
from src.agent.orchestrators import WorkflowOrchestrator
from src.agent.orchestrators.base import WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.planning import CompiledPlan, PlanCompiler, WorkflowPlan
from src.agent import runtime as runtime_module
import src.agent.tools.llm_molecular_generator as generator_module
from src.agent.runtime import workflow_executor as workflow_executor_module
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.task_state import TaskEventType
from src.agent.runtime.workflow_executor import WorkflowExecutor
from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator
from src.agent.workflows import WorkflowPolicy


FAKE_POLICY = WorkflowPolicy(
    name="comprehensive_evaluation",
    description="test workflow",
    allowed_tools=("property_calculator",),
)


@pytest.mark.parametrize(
    "script",
    [
        "from src.agent.planning import WorkflowPlan\n"
        "from src.agent.runtime import PreparedWorkflow\n"
        "assert WorkflowPlan and PreparedWorkflow\n",
        "import src.agent.runtime as runtime\n"
        "from src.agent.planning import WorkflowPlan\n"
        "assert runtime.PreparedWorkflow and WorkflowPlan\n",
    ],
)
def test_runtime_prepared_workflow_lazy_export_is_import_order_safe(script):
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        return {
            "success": True,
            "message": "ok",
            "data": {"query": query},
            "formatted": self.name,
        }


class CapturingGeneratorModel:
    model_name = "gmm-llama:latest"

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, temperature=0.7, max_tokens=1000):
        self.prompts.append(prompt)
        return "\n".join("C" * length for length in range(1, 11))


def test_legacy_target_binding_reaches_real_generator_with_canonical_count(
    monkeypatch,
):
    target = FakeTool("target_database_search")
    model = CapturingGeneratorModel()
    generator = LLMMolecularGenerator(llm_model=model)
    monkeypatch.setattr(generator, "_check_rdkit", lambda _result: True)
    monkeypatch.setattr(generator, "validate_smiles", lambda _smiles: True)
    monkeypatch.setattr(generator_module, "RDKIT_AVAILABLE", False)

    class AcceptingChem:
        @staticmethod
        def MolFromSmiles(smiles):
            return smiles

        @staticmethod
        def MolToSmiles(molecule):
            return molecule

    monkeypatch.setitem(
        sys.modules,
        "rdkit",
        SimpleNamespace(Chem=AcceptingChem),
    )
    plan = WorkflowPlan(
        workflow_name="legacy_bound_generation",
        steps=[
            WorkflowStep(
                "target",
                "target_database_search",
                input_data="PDE5A",
                output_key="target",
                capability="target.structure.search",
            ),
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_from="target",
                input_template="{query}: {input}",
                output_key="molecules",
                capability="molecule.generate",
            ),
        ],
        metadata={"requested_count": 7},
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="Design candidates for PDE5A",
            trace_id="legacy-real-generator",
            active_skill="legacy_bound_generation",
            mol_count=7,
            metadata={"requested_count": 7},
        ),
        policy=WorkflowPolicy(
            name="legacy_bound_generation",
            description="test",
            allowed_tools=(
                "target_database_search",
                "llm_molecular_generator",
            ),
        ),
        all_tools={
            "target_database_search": target,
            "llm_molecular_generator": generator,
        },
        plan=plan,
    )

    assert execution.result.success is True
    generation_result = next(
        item
        for item in execution.result.tool_results
        if item.tool_name == "llm_molecular_generator"
    )
    assert generation_result.quality["requested_count"] == 7
    assert len(model.prompts) == 1


@pytest.mark.parametrize(
    "context_kwargs, field",
    [
        ({"mol_count": 11}, "mol_count"),
        (
            {"mol_count": 11, "metadata": {"requested_count": 5}},
            "mol_count",
        ),
        ({"metadata": {"requested_count": 11}}, "requested_count"),
    ],
)
def test_public_preflight_validates_context_counts_before_compilation(
    context_kwargs,
    field,
):
    class CompilerMustNotRun:
        def compile(self, _plan, _policy):
            raise AssertionError("compiler ran before context count validation")

    tool = FakeTool("property_calculator")
    plan = WorkflowPlan(
        workflow_name="comprehensive_evaluation",
        steps=[
            WorkflowStep(
                "properties",
                "property_calculator",
                input_data="CCO",
            )
        ],
    )
    failure = WorkflowExecutor(compiler=CompilerMustNotRun()).preflight(
        context=AgentContext(
            query="Analyze CCO",
            trace_id=f"public-preflight-invalid-{field}",
            active_skill="comprehensive_evaluation",
            **context_kwargs,
        ),
        policy=FAKE_POLICY,
        all_tools={tool.name: tool},
        plan=plan,
    )

    assert failure is not None
    assert failure.result.success is False
    assert failure.result.error.code == AgentErrorCode.INVALID_INPUT
    assert failure.result.error.details["field"] == field
    assert failure.tool_attempt_count == 0
    assert failure.events == []
    assert tool.calls == []


@pytest.mark.parametrize(
    "context_kwargs, field",
    [
        ({"mol_count": 11}, "mol_count"),
        ({"metadata": {"requested_count": 11}}, "requested_count"),
    ],
)
def test_public_preflight_validates_context_before_planning(
    context_kwargs,
    field,
):
    class PlannerMustNotRun:
        def plan(self, _context):
            raise AssertionError("planner ran before context count validation")

    failure = WorkflowExecutor(planner=PlannerMustNotRun()).preflight(
        context=AgentContext(
            query="Analyze CCO",
            trace_id=f"public-preflight-before-planning-{field}",
            active_skill="comprehensive_evaluation",
            **context_kwargs,
        ),
        policy=FAKE_POLICY,
        all_tools={"property_calculator": FakeTool("property_calculator")},
    )

    assert failure is not None
    assert failure.result.error.code == AgentErrorCode.INVALID_INPUT
    assert failure.result.error.details["field"] == field


def test_non_generation_plan_rejects_invalid_context_count_before_tool():
    tool = FakeTool("property_calculator")
    plan = WorkflowPlan(
        workflow_name="comprehensive_evaluation",
        steps=[
            WorkflowStep(
                "properties",
                "property_calculator",
                input_data="CCO",
            )
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="Analyze CCO",
            trace_id="non-generation-invalid-context-count",
            active_skill="comprehensive_evaluation",
            metadata={"requested_count": 11},
        ),
        policy=FAKE_POLICY,
        all_tools={tool.name: tool},
        plan=plan,
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.INVALID_INPUT
    assert execution.tool_attempt_count == 0
    assert execution.events == []
    assert tool.calls == []


def test_non_generation_plan_accepts_harmless_valid_context_count():
    tool = FakeTool("property_calculator")
    plan = WorkflowPlan(
        workflow_name="comprehensive_evaluation",
        steps=[
            WorkflowStep(
                "properties",
                "property_calculator",
                input_data="CCO",
            )
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="Analyze CCO",
            trace_id="non-generation-valid-context-count",
            active_skill="comprehensive_evaluation",
            metadata={"requested_count": 5},
        ),
        policy=FAKE_POLICY,
        all_tools={tool.name: tool},
        plan=plan,
    )

    assert execution.result.success is True
    assert execution.tool_attempt_count == 1
    assert tool.calls == ["CCO"]


@pytest.mark.parametrize("metadata", [{}, {"requested_count": 5}])
def test_non_generation_plan_rejects_invalid_context_mol_count_before_tool(
    metadata,
):
    tool = FakeTool("property_calculator")
    plan = WorkflowPlan(
        workflow_name="comprehensive_evaluation",
        steps=[
            WorkflowStep(
                "properties",
                "property_calculator",
                input_data="CCO",
            )
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="Analyze CCO",
            trace_id="non-generation-invalid-context-mol-count",
            active_skill="comprehensive_evaluation",
            mol_count=11,
            metadata=metadata,
        ),
        policy=FAKE_POLICY,
        all_tools={tool.name: tool},
        plan=plan,
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.INVALID_INPUT
    assert execution.tool_attempt_count == 0
    assert execution.events == []
    assert tool.calls == []


def test_valid_context_mol_count_does_not_override_metadata_precedence():
    tool = FakeTool("property_calculator")
    plan = WorkflowPlan(
        workflow_name="comprehensive_evaluation",
        steps=[
            WorkflowStep(
                "properties",
                "property_calculator",
                input_data="CCO",
            )
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="Analyze CCO",
            trace_id="non-generation-valid-context-mol-count",
            active_skill="comprehensive_evaluation",
            mol_count=7,
            metadata={"requested_count": 5},
        ),
        policy=FAKE_POLICY,
        all_tools={tool.name: tool},
        plan=plan,
    )

    assert execution.result.success is True
    assert execution.tool_attempt_count == 1
    assert execution.plan.metadata == {}
    assert tool.calls == ["CCO"]


def test_valid_context_mol_count_preserves_generation_metadata_precedence():
    generator = FakeTool("llm_molecular_generator")
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data="Generate candidates",
                capability="molecule.generate",
            )
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="Generate 3 molecules",
            trace_id="valid-context-mol-count-precedence",
            active_skill="molecular_design",
            mol_count=7,
            metadata={"requested_count": 5},
        ),
        policy=WorkflowPolicy(
            name="molecular_design",
            description="test",
            allowed_tools=("llm_molecular_generator",),
        ),
        all_tools={generator.name: generator},
        plan=plan,
    )

    assert execution.tool_attempt_count == 1
    assert execution.plan.metadata["requested_count"] == 5
    assert generator.calls[0]["metadata"]["requested_count"] == 5


def test_atomic_count_conflict_fails_preflight_before_tool():
    tool = FakeTool("llm_molecular_generator")
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        metadata={"requested_count": 7, "atomic": True},
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data={
                    "query": "Generate candidates",
                    "metadata": {"requested_count": 3},
                    "outputs": {},
                },
                capability="molecule.generate",
            )
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="Generate candidates",
            trace_id="atomic-count-conflict",
            active_skill="molecular_design",
            metadata={"requested_count": 7},
        ),
        policy=WorkflowPolicy(
            name="molecular_design",
            description="test",
            allowed_tools=("llm_molecular_generator",),
        ),
        all_tools={"llm_molecular_generator": tool},
        plan=plan,
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.VALIDATION_ERROR
    assert tool.calls == []


def test_atomic_legacy_input_executes_compiler_canonical_plan_count():
    tool = FakeTool("llm_molecular_generator")
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        metadata={"requested_count": 7, "atomic": True},
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data="Generate candidates",
                capability="molecule.generate",
            )
        ],
    )
    prepared = WorkflowExecutor(
        orchestrator=CanonicalPlanRecordingOrchestrator()
    ).prepare(
        context=AgentContext(
            query="Generate candidates",
            trace_id="atomic-legacy-canonical",
            active_skill="molecular_design",
        ),
        policy=WorkflowPolicy(
            name="molecular_design",
            description="test",
            allowed_tools=("llm_molecular_generator",),
        ),
        all_tools={"llm_molecular_generator": tool},
        plan=plan,
    )

    assert prepared.plan.steps[0].input_data == "Generate candidates"
    assert prepared.compiled.plan.steps[0].input_data["metadata"] == {
        "requested_count": 7
    }

    result = prepared._run()
    execution = prepared.to_execution(result)

    assert result.success is True
    assert tool.calls[0]["metadata"] == {"requested_count": 7}
    assert execution.plan.steps[0].input_data["metadata"] == {
        "requested_count": 7
    }


def test_context_count_is_preflighted_when_generation_plan_omits_count():
    target = FakeTool("target_database_search")
    generator = FakeTool("llm_molecular_generator")
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        steps=[
            WorkflowStep(
                "target_search",
                "target_database_search",
                input_data="PDE5A",
                output_key="target",
                capability="target.structure.search",
            ),
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_binding="$.workflow",
                capability="molecule.generate",
                preconditions=("target_evidence",),
            ),
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="Design candidates for PDE5A",
            trace_id="context-invalid-count",
            active_skill="target_driven_design",
            metadata={"requested_count": 11},
        ),
        policy=WorkflowPolicy(
            name="target_driven_design",
            description="test",
            allowed_tools=(
                "target_database_search",
                "llm_molecular_generator",
            ),
        ),
        all_tools={
            target.name: target,
            generator.name: generator,
        },
        plan=plan,
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.INVALID_INPUT
    assert execution.tool_attempt_count == 0
    assert target.calls == []
    assert generator.calls == []


def test_query_count_is_preflighted_when_supplied_generation_plan_omits_count():
    target = FakeTool("target_database_search")
    generator = FakeTool("llm_molecular_generator")
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        steps=[
            WorkflowStep(
                "target_search",
                "target_database_search",
                input_data="PDE5A",
                output_key="target",
                capability="target.structure.search",
            ),
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_binding="$.workflow",
                capability="molecule.generate",
                preconditions=("target_evidence",),
            ),
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="Generate 11 molecules for PDE5A",
            trace_id="query-invalid-count",
            active_skill="target_driven_design",
        ),
        policy=WorkflowPolicy(
            name="target_driven_design",
            description="test",
            allowed_tools=("target_database_search", "llm_molecular_generator"),
        ),
        all_tools={target.name: target, generator.name: generator},
        plan=plan,
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.INVALID_INPUT
    assert execution.tool_attempt_count == 0
    assert target.calls == []
    assert generator.calls == []


class StaticResultTool(FakeTool):
    def __init__(self, name, data):
        super().__init__(name)
        self.data = data

    def execute(self, query):
        self.calls.append(query)
        return {
            "success": True,
            "message": "ok",
            "data": self.data,
            "formatted": self.name,
        }


def test_query_count_is_materialized_into_supplied_workflow_request():
    target = StaticResultTool(
        "target_database_search",
        [
            {
                "gene_symbol": "PDE5A",
                "source_record_id": "O76074",
                "source": "UniProt",
            }
        ],
    )
    generator = FakeTool("llm_molecular_generator")
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        steps=[
            WorkflowStep(
                "target_search",
                "target_database_search",
                input_data="PDE5A",
                output_key="target",
                capability="target.structure.search",
            ),
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_binding="$.workflow",
                capability="molecule.generate",
                preconditions=("target_evidence",),
            ),
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="Generate 7 molecules for PDE5A",
            trace_id="query-valid-count",
            active_skill="target_driven_design",
        ),
        policy=WorkflowPolicy(
            name="target_driven_design",
            description="test",
            allowed_tools=("target_database_search", "llm_molecular_generator"),
        ),
        all_tools={target.name: target, generator.name: generator},
        plan=plan,
    )

    assert execution.tool_attempt_count == 2
    assert execution.plan.metadata["requested_count"] == 7
    assert generator.calls[0]["metadata"]["requested_count"] == 7


def test_structured_step_count_precedes_omitted_context_query_count():
    generator = FakeTool("llm_molecular_generator")
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data={
                    "query": "Generate 7.5 molecules",
                    "metadata": {"requested_count": 7},
                    "outputs": {},
                },
                capability="molecule.generate",
            )
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="Design candidates",
            trace_id="step-count-authoritative",
            active_skill="molecular_design",
        ),
        policy=WorkflowPolicy(
            name="molecular_design",
            description="test",
            allowed_tools=("llm_molecular_generator",),
        ),
        all_tools={generator.name: generator},
        plan=plan,
    )

    assert execution.tool_attempt_count == 1
    assert execution.plan.metadata["requested_count"] == 7
    assert generator.calls[0]["metadata"]["requested_count"] == 7


@pytest.mark.parametrize("requested_count", ["7", True, 0, 11])
def test_malformed_structured_step_count_rejects_before_tool(requested_count):
    generator = FakeTool("llm_molecular_generator")
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data={
                    "query": "Generate candidates",
                    "metadata": {"requested_count": requested_count},
                    "outputs": {},
                },
                capability="molecule.generate",
            )
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="Design candidates",
            trace_id="step-count-invalid",
            active_skill="molecular_design",
        ),
        policy=WorkflowPolicy(
            name="molecular_design",
            description="test",
            allowed_tools=("llm_molecular_generator",),
        ),
        all_tools={generator.name: generator},
        plan=plan,
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.INVALID_INPUT
    assert execution.tool_attempt_count == 0
    assert generator.calls == []


def test_context_and_structured_step_count_conflict_rejects_before_tool():
    generator = FakeTool("llm_molecular_generator")
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data={
                    "query": "Generate candidates",
                    "metadata": {"requested_count": 7},
                    "outputs": {},
                },
                capability="molecule.generate",
            )
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="Design candidates",
            trace_id="context-step-count-conflict",
            active_skill="molecular_design",
            metadata={"requested_count": 5},
        ),
        policy=WorkflowPolicy(
            name="molecular_design",
            description="test",
            allowed_tools=("llm_molecular_generator",),
        ),
        all_tools={generator.name: generator},
        plan=plan,
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.VALIDATION_ERROR
    assert execution.tool_attempt_count == 0
    assert generator.calls == []


@pytest.mark.parametrize(
    "input_data, expected_count",
    [
        ("Generate 7 molecules", 7),
        ({"query": "Generate 7 molecules", "metadata": {}, "outputs": {}}, 7),
        ("Generate 3 molecules and do not generate 11 molecules", 3),
    ],
)
def test_step_query_count_is_authoritative_before_default(
    input_data, expected_count
):
    generator = FakeTool("llm_molecular_generator")
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data=input_data,
                capability="molecule.generate",
            )
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="Design candidates",
            trace_id="step-query-count",
            active_skill="molecular_design",
        ),
        policy=WorkflowPolicy(
            name="molecular_design",
            description="test",
            allowed_tools=("llm_molecular_generator",),
        ),
        all_tools={generator.name: generator},
        plan=plan,
    )

    assert execution.tool_attempt_count == 1
    assert execution.plan.metadata["requested_count"] == expected_count
    assert generator.calls[0]["metadata"]["requested_count"] == expected_count


@pytest.mark.parametrize(
    "input_data",
    [
        "Generate 11 molecules",
        "Generate 7.5 molecules",
        "Generate 1 1 molecules",
        {"query": "Generate 1\u202f000 molecules", "metadata": {}, "outputs": {}},
        {
            "query": "Generate two point-five molecules",
            "metadata": {},
            "outputs": {},
        },
    ],
)
def test_invalid_step_query_count_rejects_before_any_tool(input_data):
    generator = FakeTool("llm_molecular_generator")
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data=input_data,
                capability="molecule.generate",
            )
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="Design candidates",
            trace_id="step-query-invalid",
            active_skill="molecular_design",
        ),
        policy=WorkflowPolicy(
            name="molecular_design",
            description="test",
            allowed_tools=("llm_molecular_generator",),
        ),
        all_tools={generator.name: generator},
        plan=plan,
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.INVALID_INPUT
    assert execution.tool_attempt_count == 0
    assert generator.calls == []


@pytest.mark.parametrize(
    "later_input",
    [
        {
            "query": "Generate candidates",
            "metadata": {"requested_count": 11},
            "outputs": {},
        },
        "Generate 7.5 molecules",
        {
            "query": "Generate 3 molecules and generate 4 molecules",
            "metadata": {},
        },
    ],
)
def test_every_generation_step_count_is_preflighted_atomically(later_input):
    generator = FakeTool("llm_molecular_generator")
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        steps=[
            WorkflowStep(
                "generate_valid",
                "llm_molecular_generator",
                input_data={
                    "query": "Generate candidates",
                    "metadata": {"requested_count": 3},
                    "outputs": {},
                },
                capability="molecule.generate",
            ),
            WorkflowStep(
                "generate_invalid",
                "llm_molecular_generator",
                input_data=later_input,
                capability="molecule.generate",
            ),
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="Design candidates",
            trace_id="later-step-invalid-count",
            active_skill="molecular_design",
        ),
        policy=WorkflowPolicy(
            name="molecular_design",
            description="test",
            allowed_tools=("llm_molecular_generator",),
        ),
        all_tools={generator.name: generator},
        plan=plan,
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.INVALID_INPUT
    assert execution.tool_attempt_count == 0
    assert generator.calls == []


def test_valid_multi_step_count_conflict_remains_validation_error():
    generator = FakeTool("llm_molecular_generator")
    plan = WorkflowPlan(
        workflow_name="molecular_design",
        steps=[
            WorkflowStep(
                "generate_three",
                "llm_molecular_generator",
                input_data={
                    "query": "Generate candidates",
                    "metadata": {"requested_count": 3},
                    "outputs": {},
                },
                capability="molecule.generate",
            ),
            WorkflowStep(
                "generate_four",
                "llm_molecular_generator",
                input_data={
                    "query": "Generate candidates",
                    "metadata": {"requested_count": 4},
                    "outputs": {},
                },
                capability="molecule.generate",
            ),
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="Design candidates",
            trace_id="multi-step-count-conflict",
            active_skill="molecular_design",
        ),
        policy=WorkflowPolicy(
            name="molecular_design",
            description="test",
            allowed_tools=("llm_molecular_generator",),
        ),
        all_tools={generator.name: generator},
        plan=plan,
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.VALIDATION_ERROR
    assert execution.tool_attempt_count == 0
    assert generator.calls == []


class SingleStepPlanner:
    def plan(self, context):
        return WorkflowPlan(
            workflow_name="single_step",
            steps=[
                WorkflowStep(
                    name="barrier_step",
                    tool_name="property_calculator",
                    input_data=context.query,
                )
            ],
        )


class BarrierTool(FakeTool):
    def __init__(self, barrier):
        super().__init__("property_calculator")
        self.barrier = barrier

    def execute(self, query):
        self.barrier.wait(timeout=5)
        return super().execute(query)


class SessionRecordingOrchestrator(WorkflowOrchestrator):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.create_session_calls = []

    def create_session(self, **kwargs):
        self.create_session_calls.append(kwargs)
        return "recorded-session"


class InputRecordingOrchestrator(WorkflowOrchestrator):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.create_session_calls = []

    def create_session(self, **kwargs):
        self.create_session_calls.append(kwargs)
        return super().create_session(**kwargs)


class CanonicalPlanRecordingOrchestrator:
    state_store = None

    def __init__(self):
        self.calls = []

    def for_request(self, _event_bus):
        return self

    def run(self, *, context, steps, tools, **_kwargs):
        value = steps[0].input_data
        self.calls.append(value)
        raw = tools[steps[0].tool_name].execute(value)
        return AgentResult(
            trace_id=context.trace_id,
            success=raw["success"],
            message=raw["message"],
        )


class CountingPlanner(SingleStepPlanner):
    def __init__(self):
        self.calls = 0

    def plan(self, context):
        self.calls += 1
        return super().plan(context)


class CountingCompiler(PlanCompiler):
    def __init__(self):
        self.calls = 0
        self.last_compiled = None

    def compile(self, plan, policy):
        self.calls += 1
        self.last_compiled = super().compile(plan, policy)
        return self.last_compiled


class ExplodingPreflightOrchestrator:
    state_store = None

    def for_request(self, _event_bus):
        raise AssertionError("preflight failure created an orchestrator")

    def run(self, **_kwargs):
        raise AssertionError("preflight failure ran an orchestrator")


class CountingPrepareExecutor(WorkflowExecutor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.prepare_calls = 0

    def prepare(self, *args, **kwargs):
        self.prepare_calls += 1
        return super().prepare(*args, **kwargs)


class ToolVisibilityOrchestrator(WorkflowOrchestrator):
    def run(self, *args, **kwargs):
        result = super().run(*args, **kwargs)
        result.metadata["visible_tools"] = sorted(kwargs["tools"])
        return result


class BlockingPreflightExecutor(WorkflowExecutor):
    def __init__(self, failure, **kwargs):
        super().__init__(**kwargs)
        self.failure = failure
        self.preflight_calls = 0

    def preflight(self, context, policy, all_tools, plan=None):
        self.preflight_calls += 1
        return self.failure


class SuperCallingPreflightExecutor(WorkflowExecutor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.preflight_calls = 0

    def preflight(self, context, policy, all_tools, plan=None):
        self.preflight_calls += 1
        return super().preflight(context, policy, all_tools, plan)


def test_prepare_returns_compiled_request_scoped_workflow():
    first = FakeTool("property_calculator")
    unrelated = FakeTool("admet_predictor")
    plan = WorkflowPlan(
        workflow_name="prepared",
        steps=[
            WorkflowStep(
                name="first",
                tool_name="property_calculator",
                input_data="CCO",
                output_key="properties",
            ),
            WorkflowStep(
                name="second",
                tool_name="property_calculator",
                input_binding="$.outputs.properties",
            ),
        ],
    )

    prepared = WorkflowExecutor().prepare(
        context=AgentContext(query="CCO", trace_id="prepared"),
        policy=FAKE_POLICY,
        all_tools={
            "property_calculator": first,
            "admet_predictor": unrelated,
        },
        plan=plan,
    )

    assert isinstance(prepared, workflow_executor_module.PreparedWorkflow)
    assert prepared.plan.workflow_name == "prepared"
    assert prepared.compiled.dependencies == {
        "first": (),
        "second": ("first",),
    }
    assert dict(prepared.tools) == {"property_calculator": first}


def test_prepare_preserves_all_available_policy_allowed_tools():
    policy = WorkflowPolicy(
        name="policy-visible-tools",
        description="expose all authorized tools",
        allowed_tools=("property_calculator", "admet_predictor"),
    )
    context = AgentContext(query="CCO", trace_id="policy-visible-tools")
    plan = SingleStepPlanner().plan(context)
    property_tool = FakeTool("property_calculator")
    admet_tool = FakeTool("admet_predictor")
    unauthorized_tool = FakeTool("activity_predictor")
    all_tools = {
        "property_calculator": property_tool,
        "admet_predictor": admet_tool,
        "activity_predictor": unauthorized_tool,
    }
    executor = WorkflowExecutor(orchestrator=ToolVisibilityOrchestrator())

    prepared = executor.prepare(
        context=context,
        policy=policy,
        all_tools=all_tools,
        plan=plan,
    )
    execution = executor.execute(
        context=context,
        policy=policy,
        all_tools=all_tools,
        plan=plan,
    )

    assert set(prepared.tools) == {
        "property_calculator",
        "admet_predictor",
    }
    assert execution.result.metadata["visible_tools"] == [
        "admet_predictor",
        "property_calculator",
    ]
    assert "activity_predictor" not in prepared.tools


def test_plan_metadata_is_carried_into_workflow_binding_during_execution():
    policy = WorkflowPolicy(
        name="target_driven_design",
        description="test structured workflow binding",
        allowed_tools=(
            "target_database_search",
            "llm_molecular_generator",
        ),
    )
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        metadata={"requested_count": 7, "target_hint": "PDE5A"},
        steps=[
            WorkflowStep(
                "target_search",
                "target_database_search",
                input_data="PDE5A",
                output_key="target",
                capability="target.structure.search",
            ),
            WorkflowStep(
                "molecule_generation",
                "llm_molecular_generator",
                input_binding="$.workflow",
                output_key="molecules",
                capability="molecule.generate",
                preconditions=("target_evidence",),
            ),
        ],
    )
    target = StaticResultTool(
        "target_database_search",
        [
            {
                "gene_symbol": "PDE5A",
                "source_record_id": "O76074",
                "source": "UniProt",
            }
        ],
    )
    generator = StaticResultTool(
        "llm_molecular_generator",
        [{"smiles": "CCO"}],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="generate 2 molecules for PDE5A",
            trace_id="plan-metadata-workflow-binding",
            active_skill="target_driven_design",
            metadata={"requested_count": 2, "secret": "must-not-bind"},
        ),
        policy=policy,
        all_tools={
            "target_database_search": target,
            "llm_molecular_generator": generator,
        },
        plan=plan,
    )

    assert execution.tool_attempt_count == 2
    assert generator.calls[0]["query"] == "generate 2 molecules for PDE5A"
    assert generator.calls[0]["metadata"]["requested_count"] == 7
    assert "secret" not in generator.calls[0]["metadata"]
    assert execution.result.metadata["workflow_state"]["inputs"][
        "requested_count"
    ] == 7
    assert generator.calls[0]["outputs"]["target"] == [
        {
            "gene_symbol": "PDE5A",
            "source_record_id": "O76074",
            "source": "UniProt",
        }
    ]


def test_unused_policy_allowed_tool_may_be_absent():
    policy = WorkflowPolicy(
        name="unused-allowed-tool",
        description="unused allowed tools are optional",
        allowed_tools=("property_calculator", "admet_predictor"),
    )
    context = AgentContext(query="CCO", trace_id="unused-allowed-tool")

    execution = WorkflowExecutor(planner=SingleStepPlanner()).execute(
        context=context,
        policy=policy,
        all_tools={
            "property_calculator": FakeTool("property_calculator")
        },
    )

    assert execution.result.success is True
    assert execution.result.error is None


def test_prepared_tools_are_defensive_and_read_only():
    original = FakeTool("property_calculator")
    replacement = FakeTool("property_calculator")
    all_tools = {"property_calculator": original}
    prepared = WorkflowExecutor(planner=SingleStepPlanner()).prepare(
        context=AgentContext(query="CCO", trace_id="immutable-tools"),
        policy=FAKE_POLICY,
        all_tools=all_tools,
    )

    all_tools["property_calculator"] = replacement

    assert prepared.tools["property_calculator"] is original
    with pytest.raises(TypeError):
        prepared.tools["property_calculator"] = replacement


def test_prepare_snapshots_tool_registry_before_preflight():
    original = FakeTool("property_calculator")
    replacement = FakeTool("property_calculator")
    all_tools = {"property_calculator": original}

    class RegistryMutatingCompiler(PlanCompiler):
        def compile(self, plan, policy):
            compiled = super().compile(plan, policy)
            all_tools["property_calculator"] = replacement
            return compiled

    prepared = WorkflowExecutor(
        planner=SingleStepPlanner(),
        compiler=RegistryMutatingCompiler(),
    ).prepare(
        context=AgentContext(query="CCO", trace_id="tool-snapshot"),
        policy=FAKE_POLICY,
        all_tools=all_tools,
    )

    assert prepared.tools["property_calculator"] is original


def test_prepare_freezes_context_plan_and_compilation_for_execution():
    context = AgentContext(
        query="original-query",
        trace_id="original-trace",
        memory=[{"turn": {"query": "remembered"}}],
        metadata={
            "request": {"source": "original"},
            "path": Path("inputs/original.json"),
        },
    )
    step = WorkflowStep(
        name="snapshot-step",
        tool_name="property_calculator",
        input_data={"molecule": {"smiles": "CCO"}},
        metadata={"labels": ["original"]},
        preconditions=(),
    )
    plan = WorkflowPlan(
        workflow_name="snapshot-plan",
        steps=[step],
        metadata={"planning": {"source": "original"}},
    )
    tool = FakeTool("property_calculator")
    compiler = CountingCompiler()
    prepared = WorkflowExecutor(compiler=compiler).prepare(
        context=context,
        policy=FAKE_POLICY,
        all_tools={"property_calculator": tool},
        plan=plan,
    )

    context.query = "mutated-query"
    context.trace_id = "mutated-trace"
    context.memory[0]["turn"]["query"] = "mutated-memory"
    context.metadata["request"]["source"] = "mutated-context"
    plan.workflow_name = "mutated-plan"
    plan.metadata["planning"]["source"] = "mutated-plan-metadata"
    step.input_data["molecule"]["smiles"] = "N"
    step.metadata["labels"].append("mutated")
    object.__setattr__(step, "preconditions", ("target_evidence",))
    plan.steps.append(
        WorkflowStep(name="unexpected", tool_name="property_calculator")
    )
    compiler.last_compiled.dependencies["snapshot-step"] = ("unexpected",)

    result = prepared.orchestrator.run(
        context=prepared.context,
        steps=prepared.plan.steps,
        tools=prepared.tools,
        continue_on_error=False,
        idempotency_key=prepared.idempotency_key,
    )

    assert prepared.context.query == "original-query"
    assert prepared.context.trace_id == "original-trace"
    assert prepared.context.memory == [{"turn": {"query": "remembered"}}]
    assert prepared.context.metadata == {
        "request": {"source": "original"},
        "path": Path("inputs/original.json"),
    }
    assert prepared.plan.workflow_name == "snapshot-plan"
    assert prepared.plan.metadata == {"planning": {"source": "original"}}
    assert len(prepared.plan.steps) == 1
    assert prepared.plan.steps[0].input_data == {
        "molecule": {"smiles": "CCO"}
    }
    assert prepared.plan.steps[0].metadata == {"labels": ["original"]}
    assert prepared.plan.steps[0].preconditions == ()
    assert prepared.compiled.plan is not prepared.plan
    assert prepared.compiled.plan == prepared.plan
    assert prepared.compiled.dependencies == {"snapshot-step": ()}
    with pytest.raises(TypeError):
        prepared.compiled.dependencies["snapshot-step"] = ("changed",)
    assert prepared.tools["property_calculator"] is tool
    assert tool.calls == [{"molecule": {"smiles": "CCO"}}]
    assert result.trace_id == "original-trace"


def test_direct_prepared_workflow_construction_takes_a_defensive_snapshot():
    context = AgentContext(
        query="direct",
        trace_id="direct",
        metadata={"nested": {"value": 1}},
    )
    plan = WorkflowPlan(
        workflow_name="direct",
        steps=[
            WorkflowStep(
                name="direct-step",
                tool_name="property_calculator",
                input_data={"nested": {"value": 1}},
            )
        ],
        metadata={"nested": {"value": 1}},
    )
    dependencies = {"direct-step": []}
    tool = FakeTool("property_calculator")
    prepared = workflow_executor_module.PreparedWorkflow(
        context=context,
        policy=FAKE_POLICY,
        plan=plan,
        compiled=CompiledPlan(plan=plan, dependencies=dependencies),
        tools={"property_calculator": tool},
        orchestrator=SessionRecordingOrchestrator(),
        event_bus=AgentEventBus(),
    )

    context.metadata["nested"]["value"] = 2
    plan.metadata["nested"]["value"] = 2
    plan.steps[0].input_data["nested"]["value"] = 2
    dependencies["direct-step"].append("mutated")

    assert prepared.context is not context
    assert prepared.context.metadata == {"nested": {"value": 1}}
    assert prepared.plan is not plan
    assert prepared.plan.metadata == {"nested": {"value": 1}}
    assert prepared.plan.steps[0].input_data == {"nested": {"value": 1}}
    assert prepared.compiled.plan is not prepared.plan
    assert prepared.compiled.plan == prepared.plan
    assert prepared.compiled.dependencies == {"direct-step": ()}
    with pytest.raises(TypeError):
        prepared.compiled.dependencies["direct-step"] = ()
    assert prepared.tools["property_calculator"] is tool


def test_prepared_workflow_type_hints_resolve_at_runtime():
    hints = get_type_hints(workflow_executor_module.PreparedWorkflow)

    assert hints["orchestrator"] is WorkflowOrchestrator


def test_workflow_executor_init_type_hints_resolve_at_runtime():
    hints = get_type_hints(WorkflowExecutor.__init__)

    assert hints["orchestrator"] == WorkflowOrchestrator | None


def test_compiled_plan_dependencies_type_contract_is_mapping():
    hints = get_type_hints(CompiledPlan)

    assert hints["dependencies"] == Mapping[str, tuple[str, ...]]


def test_prepared_create_session_uses_preflight_inputs():
    context = AgentContext(query="CCO", trace_id="prepared-session")
    tool = FakeTool("property_calculator")
    prepared = WorkflowExecutor(
        planner=SingleStepPlanner(),
        orchestrator=SessionRecordingOrchestrator(),
    ).prepare(
        context=context,
        policy=FAKE_POLICY,
        all_tools={"property_calculator": tool},
        idempotency_key="prepared-idempotency",
    )

    session = prepared.create_session()

    assert session == "recorded-session"
    assert prepared.orchestrator.create_session_calls == [
        {
            "context": context,
            "steps": prepared.plan.steps,
            "tools": prepared.tools,
            "continue_on_error": False,
            "idempotency_key": "prepared-idempotency",
        }
    ]


def test_prepared_public_mutation_cannot_change_actual_execution_inputs():
    context = AgentContext(
        query="stable-query",
        trace_id="stable-trace",
        metadata={"request": {"source": "stable"}},
    )
    plan = WorkflowPlan(
        workflow_name="stable-plan",
        steps=[
            WorkflowStep(
                name="stable-step",
                tool_name="property_calculator",
                input_data={"molecule": {"smiles": "CCO"}},
                metadata={"labels": ["stable"]},
            )
        ],
        metadata={"planning": {"source": "stable"}},
    )
    tool = FakeTool("property_calculator")
    prepared = WorkflowExecutor(
        orchestrator=InputRecordingOrchestrator(),
    ).prepare(
        context=context,
        policy=FAKE_POLICY,
        all_tools={"property_calculator": tool},
        plan=plan,
    )

    prepared.context.query = "tampered-query"
    prepared.context.trace_id = "tampered-trace"
    prepared.context.metadata["request"]["source"] = "tampered"
    prepared.plan.workflow_name = "tampered-plan"
    prepared.plan.metadata["planning"]["source"] = "tampered"
    prepared.plan.steps[0].input_data["molecule"]["smiles"] = "N"
    prepared.plan.steps[0].metadata["labels"].append("tampered")
    prepared.plan.steps.append(
        WorkflowStep(
            name="injected-step",
            tool_name="property_calculator",
            input_data={"molecule": {"smiles": "O"}},
        )
    )

    session = prepared.create_session()
    session_call = prepared.orchestrator.create_session_calls[0]
    session.start()
    session.execute_step(0)
    result = session.finish()
    execution = prepared.to_execution(result)

    assert session.context.trace_id == "stable-trace"
    assert session.context.query == "stable-query"
    assert session.context.metadata == {
        "request": {"source": "stable"}
    }
    assert len(session_call["steps"]) == 1
    assert session_call["steps"][0].input_data == {
        "molecule": {"smiles": "CCO"}
    }
    assert session_call["steps"][0].metadata == {"labels": ["stable"]}
    assert prepared.compiled.plan is not prepared.plan
    assert prepared.compiled.plan != prepared.plan
    assert prepared.compiled.plan.workflow_name == "stable-plan"
    assert prepared.compiled.plan.metadata == {
        "planning": {"source": "stable"}
    }
    assert execution.result.trace_id == "stable-trace"
    assert execution.plan is not prepared.plan
    assert execution.plan.workflow_name == "stable-plan"
    assert execution.plan.metadata == {"planning": {"source": "stable"}}
    assert len(execution.plan.steps) == 1
    assert execution.plan.steps[0].input_data == {
        "molecule": {"smiles": "CCO"}
    }
    assert execution.plan.steps[0].metadata == {"labels": ["stable"]}
    assert execution.plan.steps is not session_call["steps"]
    assert (
        execution.plan.steps[0].input_data
        is not session_call["steps"][0].input_data
    )
    assert tool.calls == [{"molecule": {"smiles": "CCO"}}]


def test_prepared_public_mutation_cannot_change_run_inputs():
    context = AgentContext(query="CCO", trace_id="stable-run")
    tool = FakeTool("property_calculator")
    prepared = WorkflowExecutor(planner=SingleStepPlanner()).prepare(
        context=context,
        policy=FAKE_POLICY,
        all_tools={"property_calculator": tool},
    )

    prepared.context.trace_id = "tampered-run"
    object.__setattr__(prepared.plan.steps[0], "input_data", "N")
    prepared.plan.steps.append(
        WorkflowStep(
            name="injected-run",
            tool_name="property_calculator",
            input_data="O",
        )
    )

    class PreparedReturningExecutor(WorkflowExecutor):
        def prepare(self, *args, **kwargs):
            return prepared

    execution = PreparedReturningExecutor().execute(
        context=context,
        policy=FAKE_POLICY,
        all_tools={"property_calculator": tool},
    )

    assert execution.result.trace_id == "stable-run"
    assert execution.plan.workflow_name == "single_step"
    assert len(execution.plan.steps) == 1
    assert execution.plan.steps[0].input_data == "CCO"
    assert tool.calls == ["CCO"]


def test_prepared_to_execution_serializes_request_events():
    prepared = WorkflowExecutor(planner=SingleStepPlanner()).prepare(
        context=AgentContext(query="CCO", trace_id="prepared-events"),
        policy=FAKE_POLICY,
        all_tools={
            "property_calculator": FakeTool("property_calculator")
        },
    )
    prepared.event_bus.emit(
        trace_id="prepared-events",
        event=TaskEventType.TASK_STARTED,
        message="started",
    )
    result = AgentResult(
        trace_id="prepared-events",
        success=True,
        message="done",
    )

    execution = prepared.to_execution(result)

    assert execution.plan is not prepared.plan
    assert execution.plan == prepared.plan
    assert execution.result is result
    assert [event["event"] for event in execution.events] == ["task_started"]


def test_prepared_session_consumption_is_one_shot_but_reporting_is_not():
    tool = FakeTool("property_calculator")
    prepared = WorkflowExecutor(
        planner=SingleStepPlanner(),
        orchestrator=InputRecordingOrchestrator(),
    ).prepare(
        context=AgentContext(query="CCO", trace_id="one-shot-session"),
        policy=FAKE_POLICY,
        all_tools={"property_calculator": tool},
    )
    provisional = AgentResult(
        trace_id="one-shot-session",
        success=True,
        message="not consumed",
    )

    first_report = prepared.to_execution(provisional)
    second_report = prepared.to_execution(provisional)
    session = prepared.create_session()
    session.start()
    session.execute_step(0)
    result = session.finish()
    completed_report = prepared.to_execution(result)
    repeated_report = prepared.to_execution(result)

    assert first_report.plan is not second_report.plan
    assert completed_report.plan is not repeated_report.plan
    assert completed_report.result is result
    assert tool.calls == ["CCO"]
    with pytest.raises(
        workflow_executor_module.PreparedWorkflowConsumedError,
        match="PreparedWorkflow has already been consumed",
    ):
        prepared.create_session()
    with pytest.raises(
        workflow_executor_module.PreparedWorkflowConsumedError,
        match="PreparedWorkflow has already been consumed",
    ):
        prepared._run()


def test_prepared_run_consumption_is_one_shot():
    tool = FakeTool("property_calculator")
    context = AgentContext(query="CCO", trace_id="one-shot-run")
    prepared = WorkflowExecutor(planner=SingleStepPlanner()).prepare(
        context=context,
        policy=FAKE_POLICY,
        all_tools={"property_calculator": tool},
    )

    class PreparedReturningExecutor(WorkflowExecutor):
        def prepare(self, *args, **kwargs):
            return prepared

    executor = PreparedReturningExecutor()
    execution = executor.execute(
        context=context,
        policy=FAKE_POLICY,
        all_tools={"property_calculator": tool},
    )
    events_before_retry = [
        event.to_dict() for event in prepared.event_bus.events
    ]

    assert execution.result.success is True
    with pytest.raises(
        workflow_executor_module.PreparedWorkflowConsumedError,
        match="PreparedWorkflow has already been consumed",
    ):
        executor.execute(
            context=context,
            policy=FAKE_POLICY,
            all_tools={"property_calculator": tool},
        )
    assert tool.calls == ["CCO"]
    assert [event.to_dict() for event in prepared.event_bus.events] == (
        events_before_retry
    )


def test_prepared_consumption_guard_is_atomic_across_threads():
    prepared = WorkflowExecutor(
        planner=SingleStepPlanner(),
        orchestrator=InputRecordingOrchestrator(),
    ).prepare(
        context=AgentContext(query="CCO", trace_id="one-shot-concurrent"),
        policy=FAKE_POLICY,
        all_tools={"property_calculator": FakeTool("property_calculator")},
    )
    barrier = Barrier(2)

    def consume():
        barrier.wait(timeout=5)
        try:
            return prepared.create_session()
        except RuntimeError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(consume) for _ in range(2)]
        outcomes = [future.result() for future in futures]

    errors = [outcome for outcome in outcomes if isinstance(outcome, RuntimeError)]
    sessions = [outcome for outcome in outcomes if not isinstance(outcome, RuntimeError)]
    assert len(sessions) == 1
    assert len(errors) == 1
    assert isinstance(
        errors[0],
        workflow_executor_module.PreparedWorkflowConsumedError,
    )
    assert str(errors[0]) == "PreparedWorkflow has already been consumed"
    assert len(prepared.orchestrator.create_session_calls) == 1


def test_prepare_calls_planner_and_compiler_once_per_request():
    planner = CountingPlanner()
    compiler = CountingCompiler()
    executor = WorkflowExecutor(planner=planner, compiler=compiler)

    prepared = executor.prepare(
        context=AgentContext(query="CCO", trace_id="counted-preflight"),
        policy=FAKE_POLICY,
        all_tools={
            "property_calculator": FakeTool("property_calculator")
        },
    )

    assert isinstance(prepared, workflow_executor_module.PreparedWorkflow)
    assert planner.calls == 1
    assert compiler.calls == 1


def test_prepare_with_plan_skips_planner_and_compiles_once():
    planner = CountingPlanner()
    compiler = CountingCompiler()
    executor = WorkflowExecutor(planner=planner, compiler=compiler)
    plan = SingleStepPlanner().plan(
        AgentContext(query="CCO", trace_id="provided-plan")
    )

    prepared = executor.prepare(
        context=AgentContext(query="CCO", trace_id="provided-plan"),
        policy=FAKE_POLICY,
        all_tools={
            "property_calculator": FakeTool("property_calculator")
        },
        plan=plan,
    )

    assert prepared.plan is not plan
    assert planner.calls == 0
    assert compiler.calls == 1


@pytest.mark.parametrize(
    ("policy", "all_tools", "expected_code"),
    [
        (
            WorkflowPolicy(
                name="deny",
                description="deny",
                allowed_tools=(),
            ),
            {"property_calculator": FakeTool("property_calculator")},
            AgentErrorCode.UNAUTHORIZED_TOOL,
        ),
        (FAKE_POLICY, {}, AgentErrorCode.TOOL_UNAVAILABLE),
    ],
)
def test_prepare_returns_existing_preflight_failure_without_orchestrator(
    policy,
    all_tools,
    expected_code,
):
    execution = WorkflowExecutor(
        planner=SingleStepPlanner(),
        orchestrator=ExplodingPreflightOrchestrator(),
    ).prepare(
        context=AgentContext(query="CCO", trace_id="preflight-failure"),
        policy=policy,
        all_tools=all_tools,
    )

    assert isinstance(execution, workflow_executor_module.WorkflowExecution)
    assert execution.result.error.code == expected_code
    assert execution.result.message == "Workflow plan failed execution preflight"
    assert execution.events == []


def test_prepare_honors_public_preflight_override_block_exactly_once():
    context = AgentContext(query="CCO", trace_id="override-block")
    plan = SingleStepPlanner().plan(context)
    failure = workflow_executor_module.WorkflowExecution(
        plan=plan,
        result=AgentResult(
            trace_id=context.trace_id,
            success=False,
            message="blocked by extension",
        ),
        events=[],
    )
    compiler = CountingCompiler()
    executor = BlockingPreflightExecutor(
        failure,
        compiler=compiler,
        orchestrator=ExplodingPreflightOrchestrator(),
    )

    prepared = executor.prepare(
        context=context,
        policy=FAKE_POLICY,
        all_tools={"property_calculator": FakeTool("property_calculator")},
        plan=plan,
    )

    assert prepared is failure
    assert executor.preflight_calls == 1
    assert compiler.calls == 0


def test_prepare_override_calling_super_compiles_only_once():
    context = AgentContext(query="CCO", trace_id="override-super")
    plan = SingleStepPlanner().plan(context)
    compiler = CountingCompiler()
    executor = SuperCallingPreflightExecutor(compiler=compiler)

    prepared = executor.prepare(
        context=context,
        policy=FAKE_POLICY,
        all_tools={"property_calculator": FakeTool("property_calculator")},
        plan=plan,
    )

    assert isinstance(prepared, workflow_executor_module.PreparedWorkflow)
    assert executor.preflight_calls == 1
    assert compiler.calls == 1


def test_prepare_capture_ignores_reentrant_preflight_from_other_executor():
    tool = FakeTool("property_calculator")
    all_tools = {"property_calculator": tool}
    inner_plan = WorkflowPlan(
        workflow_name="inner",
        steps=[
            WorkflowStep(
                name="inner-first",
                tool_name="property_calculator",
                input_data="CCO",
                output_key="inner-output",
            ),
            WorkflowStep(
                name="inner-second",
                tool_name="property_calculator",
                input_binding="$.outputs.inner-output",
            ),
        ],
    )
    inner_compiler = CountingCompiler()
    inner_executor = WorkflowExecutor(compiler=inner_compiler)

    class ReentrantPreflightExecutor(WorkflowExecutor):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.preflight_calls = 0

        def preflight(self, context, policy, all_tools, plan=None):
            self.preflight_calls += 1
            failure = inner_executor.preflight(
                context=AgentContext(query="inner", trace_id="inner"),
                policy=policy,
                all_tools=all_tools,
                plan=inner_plan,
            )
            assert failure is None
            return None

    outer_plan = WorkflowPlan(
        workflow_name="outer",
        steps=[
            WorkflowStep(
                name="outer-only",
                tool_name="property_calculator",
                input_data="CCO",
            )
        ],
    )
    outer_compiler = CountingCompiler()
    outer_executor = ReentrantPreflightExecutor(compiler=outer_compiler)

    prepared = outer_executor.prepare(
        context=AgentContext(query="outer", trace_id="outer"),
        policy=FAKE_POLICY,
        all_tools=all_tools,
        plan=outer_plan,
    )

    assert prepared.compiled.plan is not prepared.plan
    assert prepared.compiled.plan == prepared.plan
    assert prepared.compiled.dependencies == {"outer-only": ()}
    assert outer_executor.preflight_calls == 1
    assert outer_compiler.calls == 1
    assert inner_compiler.calls == 1


def test_execute_calls_prepare_once_and_consumes_prepared_workflow():
    executor = CountingPrepareExecutor(planner=SingleStepPlanner())

    execution = executor.execute(
        context=AgentContext(query="CCO", trace_id="execute-prepared"),
        policy=FAKE_POLICY,
        all_tools={
            "property_calculator": FakeTool("property_calculator")
        },
    )

    assert executor.prepare_calls == 1
    assert execution.result.success is True


def test_execute_returns_prepare_failure_without_running():
    plan = SingleStepPlanner().plan(
        AgentContext(query="CCO", trace_id="prepared-failure")
    )
    failure = workflow_executor_module.WorkflowExecution(
        plan=plan,
        result=AgentResult(
            trace_id="prepared-failure",
            success=False,
            message="blocked",
        ),
        events=[],
    )

    class FailureExecutor(WorkflowExecutor):
        def prepare(self, *args, **kwargs):
            return failure

    execution = FailureExecutor(
        planner=ExplodingPreflightOrchestrator(),
        orchestrator=ExplodingPreflightOrchestrator(),
    ).execute(
        context=AgentContext(query="CCO", trace_id="prepared-failure"),
        policy=FAKE_POLICY,
        all_tools={},
    )

    assert execution is failure


def test_runtime_exports_prepared_workflow():
    assert runtime_module.PreparedWorkflow is (
        workflow_executor_module.PreparedWorkflow
    )


def test_executor_rejects_unauthorized_plan_before_any_tool_runs():
    property_tool = FakeTool("property_calculator")
    other_tools = {
        "property_calculator": property_tool,
        "drug_likeness_assessment": FakeTool("drug_likeness_assessment"),
        "admet_predictor": FakeTool("admet_predictor"),
        "activity_predictor": FakeTool("activity_predictor"),
        "reverse_target_predictor": FakeTool("reverse_target_predictor"),
        "target_database_search": FakeTool("target_database_search"),
    }

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="全面分析 CCO",
            trace_id="unauthorized",
            active_skill="comprehensive_evaluation",
        ),
        policy=FAKE_POLICY,
        all_tools=other_tools,
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.UNAUTHORIZED_TOOL
    assert property_tool.calls == []
    assert execution.events == []


def test_executor_empty_policy_denies_every_planned_tool():
    tool = FakeTool("property_calculator")
    empty_policy = WorkflowPolicy(
        name="deny_all",
        description="deny every tool",
        allowed_tools=(),
    )

    execution = WorkflowExecutor(planner=SingleStepPlanner()).execute(
        context=AgentContext(
            query="CCO",
            trace_id="deny-all",
            active_skill=empty_policy.name,
        ),
        policy=empty_policy,
        all_tools={"property_calculator": tool},
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.UNAUTHORIZED_TOOL
    assert execution.result.error.details["unauthorized_tools"] == [
        "property_calculator"
    ]
    assert execution.result.error.details["allowed_tools"] == []
    assert tool.calls == []
    assert execution.events == []


def test_executor_runs_planner_steps_with_filtered_tools():
    full_policy = WorkflowPolicy(
        name="comprehensive_evaluation",
        description="full workflow",
        allowed_tools=(
            "property_calculator",
            "drug_likeness_assessment",
            "admet_predictor",
            "activity_predictor",
            "reverse_target_predictor",
            "target_database_search",
        ),
        is_multi_step=True,
    )

    tools = {name: FakeTool(name) for name in full_policy.allowed_tools}
    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="全面分析 CCO",
            trace_id="allowed",
            active_skill="comprehensive_evaluation",
        ),
        policy=full_policy,
        all_tools=tools,
    )

    assert execution.result.success is True
    assert [item.tool_name for item in execution.result.tool_results] == list(
        full_policy.allowed_tools
    )
    assert execution.plan.workflow_name == "comprehensive_evaluation"
    assert execution.events[0]["event"] == "task_started"


def test_executor_isolates_event_bus_for_concurrent_requests():
    original_event_bus = AgentEventBus()
    shared_orchestrator = WorkflowOrchestrator(event_bus=original_event_bus)
    executor = WorkflowExecutor(
        planner=SingleStepPlanner(),
        orchestrator=shared_orchestrator,
    )
    tool = BarrierTool(Barrier(2))
    callback_events = {"trace-a": [], "trace-b": []}

    def execute(trace_id):
        return executor.execute(
            context=AgentContext(
                query=trace_id,
                trace_id=trace_id,
                active_skill="comprehensive_evaluation",
            ),
            policy=FAKE_POLICY,
            all_tools={"property_calculator": tool},
            event_callback=callback_events[trace_id].append,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute, trace_id) for trace_id in callback_events]
        executions = [future.result(timeout=10) for future in futures]

    for trace_id, events in callback_events.items():
        assert {event.trace_id for event in events} == {trace_id}
    for execution in executions:
        assert {event["trace_id"] for event in execution.events} == {
            execution.result.trace_id
        }
    assert shared_orchestrator.event_bus is original_event_bus


def test_executor_preserves_injected_orchestrator_subclass_extensions():
    class AuditedWorkflowOrchestrator(WorkflowOrchestrator):
        def __init__(self, audit_marker, **kwargs):
            super().__init__(**kwargs)
            self.audit_marker = audit_marker
            self.audit_traces = []
            self.audit_by_trace = {}

        def run(self, *args, **kwargs):
            trace_id = kwargs["context"].trace_id
            self.audit_traces.append(trace_id)
            self.audit_by_trace[trace_id] = {"recorded": True}
            result = super().run(*args, **kwargs)
            result.metadata["audit_marker"] = self.audit_marker
            result.metadata["orchestrator_type"] = type(self).__name__
            result.metadata["audit_traces"] = list(self.audit_traces)
            result.metadata["audit_by_trace"] = dict(self.audit_by_trace)
            return result

    original_event_bus = AgentEventBus()
    shared_orchestrator = AuditedWorkflowOrchestrator(
        "configured-extension",
        event_bus=original_event_bus,
    )

    executor = WorkflowExecutor(
        planner=SingleStepPlanner(),
        orchestrator=shared_orchestrator,
    )
    executions = []
    for trace_id in ("subclass-trace-a", "subclass-trace-b"):
        executions.append(
            executor.execute(
                context=AgentContext(
                    query="CCO",
                    trace_id=trace_id,
                    active_skill="comprehensive_evaluation",
                ),
                policy=FAKE_POLICY,
                all_tools={
                    "property_calculator": FakeTool("property_calculator")
                },
            )
        )

    for execution, trace_id in zip(
        executions,
        ("subclass-trace-a", "subclass-trace-b"),
    ):
        assert execution.result.success is True
        assert execution.result.metadata["audit_marker"] == "configured-extension"
        assert execution.result.metadata["orchestrator_type"] == (
            "AuditedWorkflowOrchestrator"
        )
        assert execution.result.metadata["audit_traces"] == [trace_id]
        assert set(execution.result.metadata["audit_by_trace"]) == {trace_id}

    assert shared_orchestrator.audit_traces == []
    assert shared_orchestrator.audit_by_trace == {}
    assert shared_orchestrator.event_bus is original_event_bus


def test_orchestrator_requires_override_for_uncopyable_extension_state():
    class LockedWorkflowOrchestrator(WorkflowOrchestrator):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.extension_lock = Lock()

    original_event_bus = AgentEventBus()
    shared_orchestrator = LockedWorkflowOrchestrator(
        event_bus=original_event_bus
    )

    with pytest.raises(
        TypeError,
        match=r"LockedWorkflowOrchestrator.*override for_request",
    ):
        shared_orchestrator.for_request(AgentEventBus())

    assert shared_orchestrator.event_bus is original_event_bus


def test_callback_exception_does_not_interrupt_injected_orchestrator(tmp_path):
    state_store = SQLiteAgentStateStore(tmp_path / "agent-state.sqlite3")
    tool = FakeTool("property_calculator")

    def failing_callback(_event):
        raise RuntimeError("callback transport failed")

    execution = WorkflowExecutor(
        planner=SingleStepPlanner(),
        orchestrator=WorkflowOrchestrator(state_store=state_store),
    ).execute(
        context=AgentContext(
            query="CCO",
            trace_id="callback-injected",
            active_skill="comprehensive_evaluation",
        ),
        policy=FAKE_POLICY,
        all_tools={"property_calculator": tool},
        event_callback=failing_callback,
    )

    assert execution.result.success is True
    assert tool.calls == ["CCO"]
    assert state_store.get_run("callback-injected")["status"] == "succeeded"


def test_callback_exception_does_not_interrupt_default_orchestrator():
    def failing_callback(_event):
        raise RuntimeError("callback transport failed")

    execution = WorkflowExecutor(planner=SingleStepPlanner()).execute(
        context=AgentContext(
            query="CCO",
            trace_id="callback-default",
            active_skill="comprehensive_evaluation",
        ),
        policy=FAKE_POLICY,
        all_tools={"property_calculator": FakeTool("property_calculator")},
        event_callback=failing_callback,
    )

    assert execution.result.success is True
    assert execution.events[-1]["event"] == "task_completed"


def test_executor_rejects_uncompilable_binding_before_tool_runs():
    tool = FakeTool("property_calculator")
    plan = WorkflowPlan(
        workflow_name="comprehensive_evaluation",
        steps=[
            WorkflowStep(
                name="properties",
                tool_name="property_calculator",
                input_binding="$.outputs.missing",
            )
        ],
    )

    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="CCO",
            trace_id="invalid-binding",
            active_skill="comprehensive_evaluation",
        ),
        policy=FAKE_POLICY,
        all_tools={"property_calculator": tool},
        plan=plan,
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.VALIDATION_ERROR
    assert "missing" in execution.result.error.details["reason"]
    assert tool.calls == []
    assert execution.events == []


def test_executor_resolves_request_query_without_producer_or_metadata_leakage():
    tool = FakeTool("property_calculator")
    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="CCO",
            trace_id="request-query-binding",
            active_skill="admet_assessment",
            metadata={"password": "must-not-bind"},
        ),
        policy=WorkflowPolicy(
            name="admet_assessment",
            description="test",
            allowed_tools=("property_calculator",),
        ),
        all_tools={tool.name: tool},
        plan=WorkflowPlan(
            workflow_name="admet_assessment",
            steps=[
                WorkflowStep(
                    "properties",
                    "property_calculator",
                    input_binding="$.request.query",
                    input_transform="identity",
                )
            ],
        ),
    )

    assert execution.result.success is True
    assert tool.calls == ["CCO"]


@pytest.mark.parametrize(
    "step",
    [
        WorkflowStep(
            "invalid_selector",
            "property_calculator",
            input_binding="",
        ),
        WorkflowStep(
            "invalid_transform",
            "property_calculator",
            input_transform="raw",
        ),
    ],
)
def test_executor_rejects_invalid_binding_contract_before_any_tool(step):
    tool = FakeTool("property_calculator")
    execution = WorkflowExecutor().execute(
        context=AgentContext(
            query="CCO",
            trace_id=f"preflight-{step.name}",
            active_skill="comprehensive_evaluation",
        ),
        policy=FAKE_POLICY,
        all_tools={tool.name: tool},
        plan=WorkflowPlan(
            workflow_name="comprehensive_evaluation",
            steps=[step],
        ),
    )

    assert execution.result.success is False
    assert execution.result.error.code == AgentErrorCode.VALIDATION_ERROR
    assert execution.tool_attempt_count == 0
    assert tool.calls == []
    assert execution.events == []


def test_execution_preserves_exact_compilation_and_tool_attempt_count():
    compiler = CountingCompiler()
    tool = FakeTool("property_calculator")
    executor = WorkflowExecutor(
        planner=SingleStepPlanner(),
        compiler=compiler,
    )

    execution = executor.execute(
        context=AgentContext(
            query="CCO",
            trace_id="runtime-provenance",
            active_skill="comprehensive_evaluation",
        ),
        policy=FAKE_POLICY,
        all_tools={tool.name: tool},
    )

    assert compiler.calls == 1
    assert execution.compiled_dependencies == compiler.last_compiled.dependencies
    assert execution.tool_attempt_count == 1
