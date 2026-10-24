"""Owner-bound scientific replay also requires a settled official Pi transcript."""
import importlib
from pathlib import Path

import pytest


def checkpoint_module():
    path = Path(__file__).parents[2] / 'src/agent/pi_runtime/checkpoint.py'
    assert path.is_file(), 'Pi checkpoints need explicit version and settlement validation'
    return importlib.import_module('src.agent.pi_runtime.checkpoint')


@pytest.mark.parametrize('saved', [{}, {'pi_protocol_revision': 0, 'pi_entries': []},
    {'pi_protocol_revision': 1, 'pi_entries': []}])
def test_missing_old_or_empty_pi_checkpoint_cannot_be_resumed(saved):
    module = checkpoint_module()
    with pytest.raises(ValueError):
        module.validate_pi_checkpoint(saved, mode='native', system_message={'role': 'system', 'content': 'test'})


def test_persisted_projection_without_pi_authority_is_rejected():
    module = checkpoint_module()
    with pytest.raises(ValueError):
        module.validate_pi_checkpoint({
            'pi_protocol_revision': 1,
            'pi_entries': [{'type': 'session', 'version': 3, 'id': 'foreign'}],
            'messages': [{'role': 'system', 'content': 'test'}],
        }, mode='native', system_message={'role': 'system', 'content': 'test'})
