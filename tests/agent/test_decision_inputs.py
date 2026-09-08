"""Evidence references are data bindings, not model-authored tool arguments."""
import asyncio
from dataclasses import replace

import pytest

from test_decision_loop import (
    setup_loop, CountingTool, tool, finish_last, run, last_observation,
)
from src.agent.contracts import AgentContext


def downstream(messages):
    return tool('drug_likeness_assessment', {
        'input_ref': last_observation(messages)['quality']['evidence_id']})


def test_downstream_consumes_verified_smiles_not_original_query(setup_loop):
    b = setup_loop([tool(), downstream, finish_last],
                   [CountingTool(), CountingTool('drug_likeness_assessment')])
    result = run(b, required_tools={'property_calculator', 'drug_likeness_assessment'})
    assert result.success, result.metadata
    assert b.tools[1].inputs == ['CCO']
    first, second = result.tool_results
    assert second.quality['input_evidence_ids'] == [first.quality['evidence_id']]
    assert second.provenance.input_digest != first.provenance.input_digest
    assert first.provenance.output_digest


@pytest.mark.parametrize('mode', ['foreign', 'failed', 'demo', 'invalid', 'tampered', 'no_digest', 'rehash'])
def test_untrusted_evidence_is_rejected_before_downstream(setup_loop, mode):
    class Source(CountingTool):
        def execute(self, query):
            result = super().execute(query)
            if mode == 'invalid':
                result.data['smiles'] = 'CC(C)(('
            self.last = result
            return result
    source = Source(fail=mode == 'failed', demo=mode == 'demo')
    def attempt(messages):
        if mode == 'tampered':
            source.last.data['smiles'] = 'CCN'
        if mode == 'no_digest':
            source.last.provenance = replace(source.last.provenance, output_digest=None)
        if mode == 'rehash':
            from src.agent.evidence import EvidenceLedger
            source.last.data['smiles'] = 'CCN'
            source.last.provenance = replace(source.last.provenance,
                output_digest=EvidenceLedger.output_digest(source.last.data))
        return (tool('drug_likeness_assessment', {'input_ref': 'evidence-other-trace'})
                if mode == 'foreign' else downstream(messages))
    b = setup_loop([tool(), attempt, finish_last], [source, CountingTool('drug_likeness_assessment')])
    result = run(b)
    assert not result.success
    assert b.tools[1].inputs == []


def test_cache_key_includes_resolved_input(setup_loop):
    def same_tool_bound(messages):
        return tool(arguments={'input_ref': last_observation(messages)['quality']['evidence_id']})
    b = setup_loop([tool(), same_tool_bound, finish_last])
    result = run(b)
    assert result.success
    assert b.tools[0].inputs == ['SMILES: CCO', 'CCO']
    assert len(result.tool_results) == 2


@pytest.mark.parametrize('query, count', [('SMILES: CCO', 1), ('SMILES: CCO\nSMILES: CCN', 2)])
def test_real_rdkit_property_to_drug_likeness(setup_loop, query, count):
    from src.agent.tools.property_calculator import PropertyCalculator
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    b = setup_loop([tool(), downstream, finish_last], [PropertyCalculator(), DrugLikenessAssessment()])
    result = asyncio.run(b.loop.run(AgentContext(query, 'real-binding'), request_kind='scientific',
        allowed_tools={'property_calculator', 'drug_likeness_assessment'},
        required_tools={'property_calculator', 'drug_likeness_assessment'}, event_bus=b.bus))
    assert result.success, result.metadata
    assert len(result.tool_results) == 2
    assert all(r.quality['validated'] for r in result.tool_results)
    assert all(len(r.data) == count for r in result.tool_results)
    first, second = result.tool_results
    assert [r['smiles'] for r in second.data] == [r['smiles'] for r in first.data]
    assert second.quality['input_evidence_ids'] == [first.quality['evidence_id']]


def test_final_answer_cannot_render_mutated_tool_data(setup_loop):
    class Source(CountingTool):
        def execute(self, query):
            self.last = super().execute(query)
            return self.last
    source = Source()
    def mutate_then_finish(messages):
        source.last.data['molecular_weight'] = 999999
        return finish_last(messages)
    result = run(setup_loop([tool(), mutate_then_finish], [source]))
    assert not result.success
    assert '999999' not in result.final_answer


def test_failed_observation_cannot_be_relabelled_as_success(setup_loop):
    from src.agent.contracts import ObservationStatus
    class Source(CountingTool):
        def execute(self, query):
            self.last = super().execute(query)
            return self.last
    source = Source(fail=True)
    def relabel(messages):
        source.last.success = True
        source.last.status = ObservationStatus.SUCCEEDED
        source.last.error = None
        return finish_last(messages)
    result = run(setup_loop([tool(), relabel], [source]))
    assert not result.success


def test_mutated_evidence_identity_fails_closed_without_lifecycle_exception(setup_loop):
    class Source(CountingTool):
        def execute(self, query):
            self.last = super().execute(query)
            return self.last
    source = Source()
    def mutate(messages):
        source.last.quality['evidence_id'] = 'evidence-unknown'
        return finish_last(messages)
    result = run(setup_loop([tool(), mutate], [source]))
    assert not result.success
