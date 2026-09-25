"""Narrow offline protocol helpers; no duplicate app, route or admission body."""
import json

import httpx

from test_web_decision_runtime import (
    actual_app, ActualSocket, cookie_for, result_of, chat_decision, protocol_response,
)


def intent_http_response(kind='capability', *, wire='native', history_relation='none', unresolved=False):
    """Task2's strict envelope over the real adapter's MockTransport seam."""
    raw = json.dumps({'intent': {'version': '1', 'kind': kind,
                                'history_relation': history_relation, 'unresolved': unresolved}})
    message = {'role': 'assistant', 'content': raw}
    if wire == 'native':
        message.update(content=None, tool_calls=[{'id': 'fixture-intent', 'type': 'function',
            'function': {'name': 'ordinary_intent', 'arguments': raw}}])
    return httpx.Response(200, json={'choices': [{'message': message,
        'finish_reason': 'tool_calls' if wire == 'native' else 'stop'}]})


class FakeClock:
    def __init__(self, value=100.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        assert seconds >= 0
        self.value += seconds
