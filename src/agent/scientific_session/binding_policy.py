"""Closed profile composition only; execution and lifecycle remain shared."""
from dataclasses import dataclass
import math

from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION

WEB_PROFILE = 'medchat-web-v1'


@dataclass(frozen=True)
class BindingPolicy:
    profile: str
    checkpoint_revision: int

    @property
    def is_web(self):
        return self.profile == WEB_PROFILE

    @property
    def tools(self):
        if self.is_web:
            from src.agent.contracts.web_requirements import WEB_TOOLS
            return WEB_TOOLS
        from src.agent.contracts.decision_bindings import B1_TOOLS
        return B1_TOOLS

    def prepare_requirements(self, value, **kwargs):
        if self.is_web:
            from .web_requirements import prepare_web_requirements
            return prepare_web_requirements(value, **kwargs)
        from .decision_bindings import prepare_binding_requirements
        return prepare_binding_requirements(value, **kwargs)

    def required_tools(self, requirements, required_tools):
        if self.is_web:
            from .web_requirements import required_web_tools
            return required_web_tools(requirements, required_tools)
        from src.agent.contracts.binding_requirements import required_binding_tools
        return required_binding_tools(requirements, required_tools)

    def resolver(self, **kwargs):
        if self.is_web:
            from .web_bindings import WebBindingResolver
            return WebBindingResolver(**kwargs)
        from .decision_bindings import B1BindingResolver
        return B1BindingResolver(**kwargs)

    def acceptance(self, resolver, **kwargs):
        if self.is_web:
            from .web_acceptance import evaluate_web_acceptance
            return evaluate_web_acceptance(resolver, **kwargs)
        from .decision_binding_acceptance import evaluate_binding_acceptance
        return evaluate_binding_acceptance(resolver, **kwargs)

    def render(self, resolver, **kwargs):
        if self.is_web:
            from .web_acceptance import render_web_scientific_answer
            return render_web_scientific_answer(resolver, **kwargs)
        from .decision_binding_acceptance import render_binding_scientific_answer
        return render_binding_scientific_answer(resolver, **kwargs)

    def step_metadata(self, resolver, action):
        if not self.is_web:
            return {}
        method = getattr(resolver, 'step_metadata', None)
        value = method(action) if method is not None else {}
        if (type(value) is not dict or set(value) - {'web_step_ref', 'web_plan_sha256'}
                or any(type(v) is not str or not 1 <= len(v) <= 128 for v in value.values())):
            from .decision_policy import DecisionBoundaryError
            raise DecisionBoundaryError('invalid_dynamic_binding')
        return dict(value)

    def step_timeout(self, resolver, action, remaining):
        if not self.is_web:
            return remaining
        value = resolver.step_timeout(action, remaining)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            from .decision_policy import DecisionBoundaryError
            raise DecisionBoundaryError('invalid_dynamic_binding')
        return min(value, remaining)

    def prepare_restored_observation(self, resolver, result, step):
        # Already accepted/aligned Web observations must not be aligned again.
        # Selection is profile-owned, never inferred from untrusted quality.
        if self.is_web:
            return resolver.prepare_restored_observation(result, step)
        return resolver.prepare_observation(result, step)


_B1 = BindingPolicy(B1_PROFILE_REVISION, 11)
_WEB = BindingPolicy(WEB_PROFILE, 13)


def get_binding_policy(profile):
    if profile is None:
        return None
    if type(profile) is str:
        if profile == B1_PROFILE_REVISION:
            return _B1
        if profile == WEB_PROFILE:
            return _WEB
    raise ValueError('invalid_binding_profile')
