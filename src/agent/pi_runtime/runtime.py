"""Composition root for the sole general-purpose Web Agent runtime."""
from __future__ import annotations

import math
from src.agent.scientific_session.binding_policy import get_binding_policy
from src.agent.scientific_session.decision_bounds import configuration_generation as validate_generation
from .bridge import PiRuntimeBridge


class PiAgentRuntime:
    """Pi owns iteration/context; MedChat owns domain safety and durable results."""

    def __init__(self, model, registry, state_store, *, mode='native',
                 max_model_requests=16, max_tool_attempts=12, timeout_seconds=300,
                 config_generation=None, binding_profile=None):
        get_binding_policy(binding_profile)
        self.binding_profile = binding_profile
        self.config_generation = validate_generation(config_generation)
        if mode not in {'native', 'json'}:
            raise ValueError('unsupported decision mode')
        for value, limit in ((max_model_requests, 16), (max_tool_attempts, 12)):
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError('invalid decision budget')
        if (type(timeout_seconds) not in (int, float)
                or not 0 < timeout_seconds <= 300 or not math.isfinite(timeout_seconds)):
            raise ValueError('invalid decision deadline')
        if state_store is None:
            raise ValueError('durable state store is required')
        self.model, self.registry, self.store = model, registry, state_store
        self.mode, self.max_model_requests = mode, max_model_requests
        self.max_tool_attempts, self.timeout_seconds = max_tool_attempts, timeout_seconds


    async def run(self, context, **kwargs):
        from src.agent.scientific_session.controller import ScientificSession
        return await ScientificSession(self).run(context, **kwargs)

    async def run_session(self, options, *, request_model, execute_tool):
        return await PiRuntimeBridge().run(options, request_model=request_model,
                                          execute_tool=execute_tool)
