"""Replay offline /ws turns and their owned detail responses through the homepage."""
import asyncio
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from pi_web_support import PlannedWebModel
from test_home_partial_cases import CASES
from test_reverse_receipt_consumption import strict_source_factory
from test_target_driven_design_workflow import FakeTool
from test_web_decision_runtime import ActualSocket, actual_app, cookie_for, result_of
from src.agent.tools import get_core_tools
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.tools.reverse_target_tool import ReverseTargetTool


@pytest.mark.parametrize('faults', [
    # All three rounds return a result, with zero or one mismatch per round.
    (0, 0, 0), (0, 0, 1), (0, 1, 0), (0, 1, 1),
    (1, 0, 0), (1, 0, 1), (1, 1, 0), (1, 1, 1),
    # Two mismatches end the comparison in the first, second or third round.
    (2,),
    (0, 2), (1, 2),
    (0, 0, 2), (0, 1, 2), (1, 0, 2), (1, 1, 2),
], ids=lambda counts: 'mismatches-' + '-'.join(map(str, counts)))
def test_home_repeat_correction_matrix(faults, actual_app, strict_source_factory, monkeypatch):
    query = CASES['DIVERSE-020']
    properties = PropertyCalculator()
    calculate = Mock(wraps=properties.execute)
    monkeypatch.setattr(properties, 'execute', calculate)
    admet = FakeTool('admet_predictor')
    admet.version = '2'  # The real tool's version differs from the decision protocol.
    tools = [properties, FakeTool('drug_likeness_assessment'), admet,
             FakeTool('activity_predictor'), ReverseTargetTool(strict_source_factory(('[Na+]',)))]
    expected_tools = [tool.name for tool in tools]
    tools.append(FakeTool('target_database_search'))

    def registered_tools(generator, **kwargs):
        return {tool.name: tool for tool in [*get_core_tools(generator), *tools]}.values()

    monkeypatch.setattr('src.agent.tools.get_all_tools', registered_tools)

    async def exercise():
        model = PlannedWebModel()
        remaining = 0

        async def decide(messages, **kwargs):
            nonlocal remaining
            response = await model.decide(messages, **kwargs)
            if remaining:
                decision = response.decision
                assert decision.tool_name == 'property_calculator'
                assert decision.arguments == {'step_ref': 'properties'}
                remaining -= 1
                return replace(response, decision=decision.model_copy(update={'tool_name': 'unknown_property_tool'}))
            return response

        rounds, details = [], {}
        async with actual_app(mode='pi', ordinary_policy='medchat-web-v1') as server:
            monkeypatch.setattr(server.model, 'decide', decide)
            store = server.app.agent_state_store
            cookie = await cookie_for(server.app)
            async with (
                ActualSocket(server.app, cookie) as socket,
                httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app.app),
                                  base_url='http://127.0.0.1') as client,
            ):
                ready = await socket.ready()
                for count in faults:
                    remaining = count
                    before = calculate.call_count
                    frames = await socket.turn(dict(type='chat', message=query, enable_tools=True,
                        enable_rag=False, result_format='summary_details_v1'))
                    result = result_of(frames)
                    trace = result['trace_id']
                    events = [f['event'] for f in frames if f['type'] == 'agent_event']
                    starts = [e['tool'] for e in events if e['event'] == 'tool_started']
                    loop = store.get_run(trace)['metadata']['decision_loop']
                    assert remaining == 0 and not model.errors
                    assert loop['protocol_repairs'] == min(count, 1)
                    calls = loop['model_calls']
                    wrong_name = [{'path': 'decision.tool.tool_name', 'type': 'literal_error'}]
                    assert [c for c in calls if c.get('schema_issues') == wrong_name] == calls[:count]
                    first_start = next((i for i, e in enumerate(events) if e['event'] == 'tool_started'), len(events))
                    repairs = [i for i, e in enumerate(events) if e['event'] == 'planning_completed'
                               and e['payload'].get('accepted') is False]
                    assert len(repairs) == min(count, 1) and all(i < first_start for i in repairs)
                    assert [r['tool_name'] for r in store.get_tool_executions(trace)] == starts
                    if count == 2:
                        assert result['status'] == 'failed' and result['metadata']['stop_reason'] == 'tool_not_authorized'
                        assert len(calls) == 2 and loop['tool_budget_reserved'] == 0
                        assert not starts and calculate.call_count == before
                    else:
                        assert result['status'] == 'partial' and result['metadata']['stop_reason'] == 'reverse_target_no_matches'
                        assert starts == expected_tools and calculate.call_count == before + 1
                        assert result['details_state'] == 'available'
                    assert frames[-1]['status'] == result['status']
                    rounds.append(frames)

                # Read the delivered snapshots only after the final round, on the same session.
                client.cookies.set('medchat_agent_session', cookie, domain='127.0.0.1', path='/')
                for frames in rounds:
                    result = result_of(frames)
                    if result['status'] == 'failed':
                        continue
                    trace, ref = result['trace_id'], result['details']
                    url = f"/api/agent/runs/{trace}/result-details/{ref['view_id']}?revision={ref['revision']}"
                    response = await client.get(url)
                    assert response.status_code == 200
                    payload = response.json()['data']['payload']
                    assert payload['trace_id'] == trace
                    assert [r['tool_name'] for r in payload['tool_result_sequence']] == expected_tools
                    details[url] = response.text
        # Check the catalog actually sent to the model, including on decisions
        # after the first two observations when ADMET is the next compiled step.
        for messages in model.messages:
            prompt = messages[0]['content']
            catalog, _ = json.JSONDecoder().raw_decode(prompt.split('Tool catalog: ', 1)[1])
            assert any(entry['name'] == 'admet_predictor' for entry in catalog)
            assert all('version' not in entry for entry in catalog)
            assert 'decision.version is always the string "1"' in prompt
        return dict(query=query, ready=ready, rounds=rounds, details=details)

    fixture = asyncio.run(exercise())
    script = Path(__file__).resolve().parents[1] / 'home_decision_runtime_test.js'
    replay = subprocess.run(['node', str(script), '--repeat-stdin'], input=json.dumps(fixture),
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert replay.returncode == 0, replay.stderr
