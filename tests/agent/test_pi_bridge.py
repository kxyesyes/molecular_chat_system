"""Real Node/Pi orchestration with scripted callbacks; not live model acceptance."""
import asyncio
from functools import wraps
import importlib
import importlib.util
import json
from pathlib import Path

import pytest


def async_test(function):
    @wraps(function)
    def run():
        return asyncio.run(function())
    return run


def bridge_module():
    path = Path(__file__).parents[2] / 'src/agent/pi_runtime/bridge.py'
    assert path.is_file(), 'the controlled Pi subprocess bridge must exist'
    return importlib.import_module('src.agent.pi_runtime.bridge')


def options():
    return dict(model={'id': 'contract', 'contextWindow': 16000, 'maxTokens': 1000},
                systemPrompt='Contract fixture.', prompt='Complete the contract.',
                tools=[{'name': 'finish_task', 'description': 'Validated finish.',
                        'parameters': {'type': 'object', 'properties': {}}}])


@async_test
async def test_actual_pi_child_routes_one_transaction_and_terminal_tool():
    module = bridge_module()
    observed = []
    async def model(request):
        observed.append(request)
        return {'toolCall': {'id': 'call-1', 'name': 'finish_task', 'arguments': {}}}
    async def tool(call):
        assert call['name'] == 'finish_task'
        return {'content': 'Contract validated', 'terminate': True}
    bridge = module.PiRuntimeBridge()
    result = await bridge.run(options(), request_model=model, execute_tool=tool)
    assert result['runtime'] == 'pi' and result['terminated'] is True
    assert len(observed) == 1
    assert bridge.active_pid is None


@async_test
async def test_callback_failure_is_not_retried_or_reported_as_success():
    module = bridge_module()
    count = 0
    async def model(_request):
        nonlocal count
        count += 1
        raise ValueError('contract_provider_failure')
    bridge = module.PiRuntimeBridge()
    with pytest.raises(ValueError, match='contract_provider_failure'):
        await bridge.run(options(), request_model=model, execute_tool=None)
    assert count == 1 and bridge.active_pid is None


@async_test
async def test_cancellation_drains_callback_then_stops_child():
    module = bridge_module()
    entered, settled = asyncio.Event(), asyncio.Event()
    async def model(_request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            settled.set()
    bridge = module.PiRuntimeBridge()
    running = asyncio.create_task(bridge.run(options(), request_model=model, execute_tool=None))
    await asyncio.wait_for(entered.wait(), timeout=30)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert settled.is_set() and bridge.active_pid is None


def test_child_environment_does_not_inherit_credentials_or_node_injection(monkeypatch):
    module = bridge_module()
    monkeypatch.setenv('OPENAI_API_KEY', 'contract-secret')
    monkeypatch.setenv('NODE_OPTIONS', '--import=untrusted.mjs')
    monkeypatch.setenv('PI_CODING_AGENT_DIR', 'untrusted')
    env = module.child_environment()
    assert env.get('PI_OFFLINE') == '1'
    assert not {'OPENAI_API_KEY', 'NODE_OPTIONS', 'PI_CODING_AGENT_DIR'} & env.keys()


@pytest.mark.parametrize('raw', [b'{}\n', b'not json\n', b'{"v":2,"type":"ready"}\n'])
def test_invalid_protocol_is_closed(raw):
    module = bridge_module()
    with pytest.raises(module.PiBridgeError):
        module.decode_frame(raw)


def test_oversized_frame_is_rejected():
    module = bridge_module()
    with pytest.raises(module.PiBridgeError, match='pi_frame_too_large'):
        module.decode_frame(b' ' * (module.MAX_FRAME_BYTES + 1))


@async_test
async def test_frame_envelope_preserves_inner_decision_depth_budget():
    """DTO/transport fixture only: nested arguments are not scientific authority."""
    from src.agent.contracts.decision import parse_decision_json, DecisionProtocolError
    from src.agent.pi_runtime.provider import decision_tool_call, tool_definitions
    nested = 'fixture'
    for _ in range(13):
        nested = {'item': nested}
    document = {'decision': {'version': '1', 'action': 'tool', 'tool_name': 'property_calculator',
                            'purpose': 'Transport fixture', 'arguments': {'payload': nested}}}
    decision = parse_decision_json(json.dumps(document))
    document['decision']['arguments']['payload'] = {'one_more': nested}
    with pytest.raises(DecisionProtocolError, match='decision_too_deep'):
        parse_decision_json(json.dumps(document))
    calls = []
    async def model(_request):
        return decision_tool_call(decision, 'depth-contract')
    async def tool(call):
        calls.append(call)
        return {'content': 'Transport fixture only', 'terminate': True}
    configured = options()
    configured['tools'] = tool_definitions(['property_calculator'])
    bridge = bridge_module().PiRuntimeBridge()
    result = await bridge.run(configured, request_model=model, execute_tool=tool)
    assert result['runtime'] == 'pi' and len(calls) == 1
    assert calls[0]['arguments']['decision'] == decision.model_dump()
    assert bridge.active_pid is None


@pytest.mark.parametrize('raw', [
    b'{"v":1,"v":1,"type":"ready"}\n',
    b'{"v":1,"type":"event","x":NaN}\n',
    b'{"v":1,"type":"event","x":"\\ud800"}\n',
])
def test_frame_codec_preserves_strict_json_rejection(raw):
    module = bridge_module()
    with pytest.raises(module.PiBridgeError, match='pi_protocol_invalid'):
        module.decode_frame(raw)


def test_frame_depth_remains_bounded():
    module = bridge_module()
    nested = None
    for _ in range(module.MAX_FRAME_DEPTH):
        nested = [nested]
    raw = (json.dumps({'v': 1, 'type': 'event', 'payload': nested}) + '\n').encode()
    with pytest.raises(module.PiBridgeError, match='pi_protocol_invalid'):
        module.decode_frame(raw)


@pytest.mark.parametrize('limit', [-1, 65, True, 16.0, None])
def test_json_codec_cannot_request_an_unbounded_depth(limit):
    from src.agent.contracts.decision import decode_protocol_json, DecisionProtocolError
    with pytest.raises(DecisionProtocolError, match='invalid_decision_options'):
        decode_protocol_json('{}', max_depth=limit)


@pytest.mark.parametrize(('scenario', 'reason', 'model_calls'), [
    ('exit', 'pi_process_exited', 0),
    ('wrong_run', 'pi_identity_mismatch', 0),
    ('skipped_id', 'pi_request_identity_invalid', 0),
    ('duplicate_id', 'pi_request_identity_invalid', 1),
    ('unknown_operation', 'pi_operation_forbidden', 0),
    ('unterminated', 'pi_terminal_missing', 0),
    ('failed', 'pi_runtime_failed', 0),
])
def test_protocol_peer_failure_closes_child_without_replay(tmp_path, scenario, reason, model_calls):
    """Adversarial protocol peer, not an official-Pi or live-model acceptance."""
    host = tmp_path / 'protocol-peer.mjs'
    host.write_text('''
import { createInterface } from 'node:readline';
const send = (frame) => process.stdout.write(JSON.stringify({v: 1, ...frame}) + '\\n');
send({type: 'ready'});
const lines = createInterface({input: process.stdin});
for await (const line of lines) {
  const start = JSON.parse(line);
  if (start.type !== 'start') continue;
  const runId = start.runId;
  const request = {type: 'request', runId, id: '1', operation: 'model', payload: {}};
  switch (SCENARIO) {
    case 'exit': process.exit(7);
    case 'wrong_run': send({...request, runId: '0'.repeat(32)}); break;
    case 'skipped_id': send({...request, id: '2'}); break;
    case 'duplicate_id': send(request); send(request); break;
    case 'unknown_operation': send({...request, operation: 'shell'}); break;
    case 'unterminated': send({type: 'result', runId, result: {runtime: 'pi', terminated: false}}); break;
    case 'failed': send({type: 'failed', runId, code: 'untrusted-raw-diagnostic'}); break;
  }
}
'''.replace('SCENARIO', json.dumps(scenario)), encoding='utf-8')
    module = bridge_module()
    calls = []
    async def model(payload):
        calls.append(payload)
        return {}
    async def execute():
        bridge = module.PiRuntimeBridge(host_path=host)
        with pytest.raises(module.PiBridgeError, match=f'^{reason}$'):
            await bridge.run(options(), request_model=model, execute_tool=None)
        assert bridge.active_pid is None
    asyncio.run(execute())
    assert len(calls) == model_calls
