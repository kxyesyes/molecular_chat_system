"""Server-injected epoch only; no credential reads, hashes or app wiring."""
import inspect

import pytest

from test_decision_loop import setup_loop, clarify, tool, finish_last
from test_decision_continuation import start, invoke, ContinuationModel, owned_context
from src.agent.harness.decision_loop import ModelDecisionLoop
from src.agent.harness.decision_continuation import configuration_digest


def supports_generation():
    assert 'config_generation' in inspect.signature(ModelDecisionLoop).parameters, 'server generation support missing'


@pytest.mark.parametrize('generation', ['', 1, True, {}, 'x'*129, 'api_key=synthetic-secret'])
def test_invalid_generation_rejected_before_any_execution(setup_loop, generation):
    supports_generation()
    with pytest.raises(ValueError): setup_loop([], config_generation=generation)


def test_fingerprint_separates_generations_without_reading_credentials(setup_loop):
    supports_generation()
    b = setup_loop([], config_generation='epoch-1')
    class Model:
        provider = 'test'; model_name = 'offline'; base_url = None
        @property
        def api_key(self): pytest.fail('credential inspected')
        @property
        def __dict__(self): pytest.fail('arbitrary model state inspected')
    b.loop.model = Model()
    def digest():
        return configuration_digest(b.loop, owned_context(), 'scientific', set(), set(), {}, {})
    first = digest()
    b.loop.config_generation = 'epoch-2'
    assert digest() != first
    b.loop.config_generation = 'epoch-1'
    assert digest() == first


@pytest.mark.parametrize('changed', [False, True])
def test_epoch_change_rejects_resume_without_consuming_waiting_record(setup_loop, changed):
    supports_generation()
    b = setup_loop([], config_generation='epoch-1')
    waiting = start(b, [clarify()])
    before = b.store.get_run('owned-trace')
    b.model = b.loop.model = ContinuationModel([tool(), finish_last])
    if changed: b.loop.config_generation = 'epoch-2'
    result = invoke(b, continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCO')
    if changed:
        assert not result.success and not b.model.messages and not b.tools[0].inputs
        assert b.store.get_run('owned-trace') == before
    else:
        assert result.success, result.metadata
