"""Retain the domain exception contract while Pi selects explicit profiles."""
import pytest

from src.agent.contracts import AgentContext
from src.agent.scientific_session.decision_policy import (
    DecisionBoundaryError, authorized_catalog, decision_system_message,
)


@pytest.mark.parametrize('profile', ['unknown', True, {}])
@pytest.mark.parametrize('boundary', ['prompt', 'catalog'])
def test_unknown_profile_retains_domain_boundary_error(profile, boundary):
    with pytest.raises(DecisionBoundaryError, match='^invalid_binding_profile$'):
        if boundary == 'prompt':
            decision_system_message('scientific', (), [], {}, binding_profile=profile)
        else:
            authorized_catalog(None, AgentContext('SMILES: CCO', 'profile-test'),
                               'scientific', (), binding_profile=profile)
