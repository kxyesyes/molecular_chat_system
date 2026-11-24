"""Homepage transport for a revision-bound goal and its verified result."""
import asyncio
import json
import time

from src.agent.persistence.redaction import contains_secret_material
from src.system.model_lifecycle import finish_on_cancel
from .prompt_budget import InputBudgetExceeded, validate_question


def goal_available(registered):
    from .goal_tools import GOAL_TOOL_NAMES
    return bool(registered & GOAL_TOOL_NAMES)


def valid_goal_text(value, limit):
    if type(value) is not str or not value.strip() or len(value) > 16384 or contains_secret_material(value):
        return False
    try:
        validate_question(value, limit)
    except InputBudgetExceeded:
        return False
    return True


async def execute_goal(runtime, handler, sender, turn, destination):
    from .goal_planning import run_goal
    from .goal_tools import GoalTools

    state = turn.goal
    trace = turn.trace_id
    owner = sender.scope['agent_session_id']
    deadline = time.monotonic() + runtime.timeout_seconds
    async def write(method, *args, **kwargs):
        return await finish_on_cancel(asyncio.to_thread(method, *args, **kwargs))

    async def progress(revision, phase, message, tool=None):
        await write(runtime.store.append_event, dict(trace_id=trace, event='goal_' + phase, tool=tool,
                    payload={'revision': revision}))
        def capture():
            if state.revision != revision or turn.cancel_event.is_set() or turn.result_started:
                return None
            return json.dumps(dict(type='goal_progress', turn_id=turn.turn_id, trace_id=trace,
                       revision=revision, phase=phase, message=message, tool=tool), ensure_ascii=False)
        await sender.send_text(None, capture=capture)

    await write(runtime.store.start_run, dict(trace_id=trace, session_id=owner, user_id=owner,
                skill_name='goal_analysis', status='running'), exclusive=True)
    status = 'failed'
    try:
        await runtime.application._refresh_llm_config_from_env()
        async with runtime._model_request(turn):
            answer = await run_goal(state, model=runtime.application.model, mode=runtime.wire_mode,
                tools=GoalTools(runtime.registry), progress=progress, cancelled=turn.cancel_event.is_set,
                input_limit=handler._input_limit(), max_model_requests=runtime.max_model_requests,
                max_tool_attempts=12, worker_owner=turn.worker_owner, deadline=deadline)
            # Seal the revision before releasing the lease or queueing delivery.
            if turn.cancel_event.is_set():
                raise asyncio.CancelledError
            turn.result_started = True
            revision = state.revision
            status = answer['status']
        turn.scientific_update = [{'user': state.goal, 'agent_used': True}]
        for frame in (dict(type='agent_result', final_answer=answer['text'], display_answer=answer['text'], metadata={}),
                      dict(type='complete', content=answer['text'])):
            frame.update(status=status, trace_id=trace, goal_revision=revision)
            await destination.send_text(json.dumps(frame, ensure_ascii=False))
    except asyncio.CancelledError:
        status = 'cancelled'
        raise
    finally:
        try:
            await write(runtime.store.update_run_metadata, trace, {
                'goal_revision': state.revision, 'goal_model_requests': state.model_requests,
                'goal_receipts': list(state.receipts)})
        finally:
            await write(runtime.store.update_run_status, trace, status)
