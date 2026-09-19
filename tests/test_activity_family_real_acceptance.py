"""The single opt-in item. Collection/disabled execution never imports models."""
import os
from pathlib import Path

import pytest


def acceptance_environment():
    return os.environ


def test_trained_family_acceptance():
    env = acceptance_environment()
    if env.get('MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE') != '1':
        pytest.skip('trained family acceptance explicitly disabled')
    from tests.family_real_acceptance_support import run_acceptance, write_report
    from uuid import uuid4
    repo = Path(__file__).absolute().parents[1]
    report = run_acceptance(env, repo_dir=repo)
    # Configuration errors are failures, not missing-dependency skips.
    if report.get('error_code') == 'invalid_configuration':
        pytest.fail('invalid_configuration', pytrace=False)
    try:
        write_report(repo / 'outputs' / 'agent_evaluation' /
                     f'family_acceptance_{uuid4().hex}.json', report)
    except ValueError:
        pytest.fail('invalid_report', pytrace=False)
    if report['status'] != 'passed':
        pytest.fail(report.get('error_code') or 'chain_mismatch', pytrace=False)
