"""Test-only /ws worker: scripted decisions, real RDKit, isolated durable stores.

The ephemeral browser cookie is passed over captured pipes, never saved or
printed by the parent. This is process recovery evidence, not live-provider,
network WebSocket, proxy, or production acceptance.
"""
import asyncio
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


async def exercise(directory, phase, pointer):
    import pytest
    from src.agent.tools.property_calculator import PropertyCalculator
    from test_decision_loop import clarify, finish, tool
    from test_web_decision_runtime import ActualSocket, actual_app, cookie_for, protocol_response, result_of

    with pytest.MonkeyPatch.context() as patch:
        for name, path in {
            'MEDCHAT_USER_CONFIG_DIR': directory / 'user-config',
            'MEDCHAT_LLM_LOCK_DIR': directory / 'llm-locks',
            'MEDCHAT_ENV_FILE': directory / 'never-loaded.env',
            'MEDCHAT_AGENT_SESSION_DB': directory / 'sessions.sqlite',
        }.items():
            patch.setenv(name, str(path))
        # Record actual invocations, not the count of serialized tool results.
        invocations = []
        execute = PropertyCalculator.execute

        def observed_execute(self, query):
            invocations.append(query)
            return execute(self, query)

        patch.setattr(PropertyCalculator, 'execute', observed_execute)
        from src.web.user_llm_config import default_user_llm_config, save_user_llm_config

        if phase in {'wait_key', 'resume_changed_key'}:
            config = dict(default_user_llm_config(), api_key=(
                'synthetic-original-key' if phase == 'wait_key' else 'synthetic-rotated-key'))
            save_user_llm_config(directory / 'user-config' / 'llm.env', config)

        async def respond(payload):
            observations = [json.loads(message['content'])
                            for message in payload['messages'] if message.get('role') == 'tool']
            if phase.startswith('wait'):
                if phase == 'wait_cached' and not observations:
                    decision = tool()
                else:
                    decision = clarify()
            elif observations:
                decision = finish([observations[-1]['quality']['evidence_id']])
            else:
                decision = tool()
            # A provider issues distinct call IDs across connections/processes.
            # The default fixture's counter starts at one in every new worker.
            return protocol_response(decision.model_dump(), call_id=uuid4().hex)

        build = actual_app.__wrapped__(directory, patch)
        async with build(mode='decision_a2', respond=respond) as case:
            if phase == 'wait_rotated':
                await case.app._persist_user_llm_config(dict(case.app.active_llm_config,
                    model_name='synthetic-rotated-model'))
            if phase == 'wait_refreshed':
                save_user_llm_config(directory / 'user-config' / 'llm.env',
                    dict(case.app.active_llm_config, model_name='synthetic-refreshed-model'))
                assert await case.app._refresh_llm_config_from_env()
            cookie = (await cookie_for(case.app) if phase.startswith('wait') or phase == 'foreign'
                      else pointer['cookie'])
            async with ActualSocket(case.app, cookie) as socket:
                assert (await socket.ready())['reconnect_supported'] is True
                if phase.startswith('wait'):
                    prompt = '计算 logP；SMILES: CCO' if phase == 'wait_cached' else '计算 logP'
                    frames = await socket.turn({'message': prompt})
                    result = result_of(frames)
                    assert result['status'] == 'waiting_for_input'
                    private_pointer = {'cookie': cookie, 'trace_id': result['trace_id'],
                                       'continuation_id': frames[-1]['continuation_id'],
                                       'cached': phase == 'wait_cached'}
                    state = case.app.agent_state_store.get_run(result['trace_id'])
                    return {'phase': phase, 'pid': os.getpid(), 'pointer': private_pointer,
                            'status': state['status'], 'invocations': len(invocations),
                            'results': result['tool_result_sequence']}

                await socket.send({'type': 'resume', 'trace_id': pointer['trace_id'],
                                   'continuation_id': pointer['continuation_id'],
                                   'message': '计算 logP；SMILES: CCO'})
                first = await socket.receive()
                if first['type'] == 'error':
                    assert first == {'type': 'error', 'code': 'continuation_unavailable'}
                    return {'phase': phase, 'pid': os.getpid(), 'status': 'rejected',
                            'invocations': len(invocations), 'model_calls': len(case.calls)}
                frames = [first]
                for _ in range(150):
                    if frames[-1]['type'] == 'complete':
                        break
                    frames.append(await socket.receive())
                result = result_of(frames)
                state = case.app.agent_state_store.get_run(pointer['trace_id'])
                events = case.app.agent_state_store.get_events(pointer['trace_id'])
                return {'phase': phase, 'pid': os.getpid(), 'status': (
                            'succeeded' if result['success'] else result['status']),
                        'stored_status': state['status'], 'model_calls': len(case.calls),
                        'success': result['success'], 'trace_id': result['trace_id'],
                        'claimed': bool(state['metadata']['decision_continuation'].get('claimed_by')),
                        'invocations': len(invocations), 'results': result['tool_result_sequence'],
                        'events': [event['event'] for event in events],
                        'event_sequences': [event['sequence'] for event in events],
                        'terminal_frames': sum(frame['type'] == 'complete' for frame in frames),
                        'answer_matches': frames[-1]['content'] == result['final_answer']}


def main():
    directory, phase = Path(sys.argv[1]), sys.argv[2]
    pointer = json.loads(sys.stdin.read())
    # The actual app's legacy constructors may write diagnostics to stdout.
    # Keep them in the captured stderr, not mixed into the one JSON response.
    with redirect_stdout(sys.stderr):
        result = asyncio.run(exercise(directory, phase, pointer))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
