"""A distinct strict proof schema, never a permissive B1 upgrade."""
from copy import deepcopy

import pytest

from src.agent.evidence import EvidenceLedger


def proof():
    return dict(version='1', profile='medchat-web-v1', policy='web-v1',
        step_ref='properties', plan_sha256='a' * 64, requirements_sha256='b' * 64,
        action_sha256='c' * 64, input_sha256='d' * 64, roles=[
            dict(role='dependency', step_ref='molecule_generation',
                 evidence_id='evidence-source', output_sha256='e' * 64)],
        own_source=None, selection_sha256=None)


def test_ledger_accepts_explicit_matching_web_proof():
    value = proof()
    assert EvidenceLedger.validated_binding_proof(value, deepcopy(value)) == value


@pytest.mark.parametrize('mode', ['profile', 'role', 'extra', 'version', 'digest', 'duplicate', 'newline'])
def test_web_proof_rejects_unknown_and_mismatched_values(mode):
    value = proof()
    if mode == 'profile':
        value['profile'] = 'ordinary-semantic-b1-v1'
    elif mode == 'role':
        value['roles'][0]['role'] = 'molecules'
    elif mode == 'extra':
        value['allow_shell'] = True
    elif mode == 'version':
        value['version'] = 1
    elif mode == 'digest':
        value['plan_sha256'] = 'a' * 63
    elif mode == 'duplicate':
        value['roles'] *= 2
    else:
        value['roles'][0]['evidence_id'] += '\n'
    with pytest.raises((ValueError, TypeError)):
        EvidenceLedger.validated_binding_proof(value, deepcopy(value))


def test_ledger_never_accepts_cross_profile_quality_proof():
    value, quality = proof(), proof()
    quality['profile'] = 'ordinary-semantic-b1-v1'
    with pytest.raises(ValueError):
        EvidenceLedger.validated_binding_proof(value, quality)
