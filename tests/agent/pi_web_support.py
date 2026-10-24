"""Actual Pi/Web execution with scripted decisions, never live model evidence.

Only the model boundary and socket transport are fixtures. Requirements, registry,
references, SQLite, Pi child, tool execution and completion publication are real.
Scientific history is server-owned and never copied into plain model history.
"""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.agent.contracts.decision import FinishDecision, ToolDecision
from src.agent.decision_transport import DecisionResponse
from src.agent.tooling.factory import build_tool_registry
from src.web.chat_handler import ChatHandler
from src.web.decision_runtime import WebDecisionRuntime, _Sender, _Turn
from src.web.model_lifecycle import ModelRequestGate
from src.web.scientific_references import ScientificReferenceService


_HANDLERS = []


class PlannedWebModel:
    """Propose each immutable compiled step once; Pi still owns the loop."""
    def __init__(self):
        self.messages = []
        self.errors = []

    async def generate(self, *args, **kwargs):
        pytest.fail('scientific presentation must not invoke model prose generation')

    async def decide(self, messages, **kwargs):
        self.messages.append(deepcopy(messages))
        try:
            prompt = messages[0]['content']
            marker = '. Immutable result requirements (not a tool sequence): '
            requirements, _ = json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])
            assert requirements['profile'] == 'medchat-web-v1'
            seen, ids = set(), []
            for message in messages:
                for call in message.get('tool_calls', []):
                    decision = json.loads(call['function']['arguments'])['decision']
                    if decision['action'] == 'tool':
                        seen.add(decision['arguments']['step_ref'])
                if message.get('role') == 'tool':
                    observation = json.loads(message['content'])
                    identity = observation.get('quality', {}).get('evidence_id')
                    if identity and identity not in ids:
                        ids.append(identity)
            step = next((step for step in requirements['steps'] if step['name'] not in seen), None)
            if step is not None:
                decision = ToolDecision(version='1', action='tool', tool_name=step['tool_name'],
                    arguments={'step_ref': step['name']}, purpose='attempt compiled business obligation')
            else:
                decision = FinishDecision(version='1', action='finish',
                    response_kind='scientific' if requirements['steps'] else 'chat',
                    text='Scripted protocol-only response; not scientific claims.', evidence_ids=ids)
            return DecisionResponse(decision, None, 'web-call-' + str(len(self.messages)),
                {'model': 'offline-business-protocol', 'request_attempts': 1})
        except Exception as exc:
            self.errors.append(exc)
            raise


class WebSocket:
    def __init__(self, session=None):
        self.scope = {'agent_session_id': session} if session is not None else {}
        self.messages = []
        self.closed = []

    async def send_text(self, text):
        self.messages.append(json.loads(text))

    async def close(self, **kwargs):
        self.closed.append(kwargs)


def make_web_handler(tools, store, model=None):
    tools = dict(tools) if isinstance(tools, dict) else {tool.name: tool for tool in tools}
    registry = build_tool_registry(list(tools.values()))
    references = ScientificReferenceService(store)
    model = model if model is not None else PlannedWebModel()

    async def refresh():
        return None

    app = SimpleNamespace(model=model, model_generation='offline-web-business-v1',
        model_request_gate=ModelRequestGate(), ordinary_chat_policy='medchat-web-v1',
        _get_agent_tool_registry=lambda: registry, _get_agent_state_store=lambda: store,
        _get_scientific_references=lambda: references, _refresh_llm_config_from_env=refresh)
    # Domain lookup helpers need the registered tool/store, not a Supervisor or
    # any execute()/should_use_tools() legacy entrypoint.
    agent = SimpleNamespace(tools=tools, tool_registry=registry, state_store=store)
    handler = ChatHandler(model, None, agent, {}, scientific_references=references)
    handler.decision_runtime = WebDecisionRuntime(app, wire_mode='native')
    handler.web_results = []
    handler.web_turns = []
    handler.web_senders = []
    original = handler.process_decision_message

    async def observe(*args, **kwargs):
        result = await original(*args, **kwargs)
        handler.web_results.append(result)
        return result

    handler.process_decision_message = observe
    _HANDLERS.append(handler)
    return handler


def run_web_turn(handler, query, history=None, session=None, **kwargs):
    """Await real completion, returning its frame and this turn's transport frames.

    Explicit list+session identifies one server socket. Missing legacy caller
    context instead creates a fresh authenticated socket, never handler-global
    memory. Tests may deliberately alias a foreign/adversarial history list.
    """
    runtime = handler.decision_runtime
    bound = type(history) is list and type(session) is str and bool(session)
    sender = next((sender for old, identity, sender in handler.web_senders
        if bound and old is history and identity == session), None)
    if sender is None:
        sender = _Sender(WebSocket(session if type(session) is str and session else 'socket-' + uuid4().hex))
        sender._runtime = runtime
        sender.scientific_memory = history if bound else []
        if bound:
            handler.web_senders.append((history, session, sender))
    start = len(sender.websocket.messages)
    payload = dict(message=query, enable_tools=True, enable_rag=False)
    payload.update(kwargs)
    turn = _Turn(payload)
    handler.web_turns.append(turn)

    async def execute():
        turn.task = asyncio.current_task()
        runtime.active_owners.add(turn)
        await runtime._execute(handler, sender, turn)
        assert turn.worker_owner.pending_roots == 0
        assert runtime.application.model_request_gate._readers == 0

    asyncio.run(execute())
    assert not getattr(handler.model, 'errors', []), getattr(handler.model, 'errors', [])
    # No typed evidence/pointers may leak into model chat-history storage.
    assert all(set(pair) == {'user', 'assistant'} for pair in sender.memory)
    frames = sender.websocket.messages[start:]
    complete = [frame for frame in frames if frame['type'] == 'complete']
    assert len(complete) == 1, frames
    assert turn.terminal_sent, frames
    return complete[0], frames


@pytest.fixture(autouse=True)
def cleanup_web_handlers():
    yield
    while _HANDLERS:
        handler = _HANDLERS.pop()
        assert not handler.decision_runtime.active_owners
        handler.agent_system.tool_registry.close()
