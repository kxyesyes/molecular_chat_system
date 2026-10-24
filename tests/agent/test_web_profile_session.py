"""Domain-bound Web actions must reach one adapter without a second planner pass."""
from copy import deepcopy

from src.agent.contracts import AgentContext
from src.agent.evidence import EvidenceLedger
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.run_session import WorkflowRunSession
from src.agent.scientific_session.decision_execution import SingleAttemptTool
from src.agent.tooling.factory import build_tool_registry
from test_generation_ranking_contract import CountingTool, GEN, output


def test_web_generation_preserves_prebound_input_exactly(tmp_path):
    tool = CountingTool(GEN, output(GEN))
    registry = build_tool_registry([tool])
    expected = {'query': {'query': 'generate 1 molecule', 'outputs': {},
                         'metadata': {'requested_count': 1, 'temperature': 0.23}}}
    dispatched = []
    session = WorkflowRunSession(
        WorkflowOrchestrator(state_store=SQLiteAgentStateStore(str(tmp_path / 'state.db'))),
        AgentContext('generate 1 molecule', 'web-input', mol_count=1, temperature=0.23),
        [], {GEN: SingleAttemptTool(registry.resolve(GEN))}, dynamic=True,
        reference_guard=lambda step, value: dispatched.append(deepcopy(value)),
    )
    session._decision_binding_profile = 'medchat-web-v1'
    try:
        session.start()
        session.append_step(WorkflowStep('decision-1', GEN, input_data=deepcopy(expected),
                                        output_key='decision-1', required=False))
        session.execute_step(0)
        assert dispatched and all(value == expected for value in dispatched)
        assert tool.inputs == [expected['query']]
        assert session.results[0].success
        assert session.results[0].provenance.input_digest == session.orchestrator._input_hash(expected)
        assert session.results[0].provenance.output_digest == EvidenceLedger.output_digest(session.results[0].data)
    finally:
        registry.close()
