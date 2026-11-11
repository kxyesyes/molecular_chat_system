from unittest.mock import Mock

import pytest

from src.agent.scientific_session import decision_bindings as bindings
from test_decision_dynamic_bindings import build, decision, execute, owned


def test_repeated_reads_reuse_record_digest_and_recheck_observation(build, monkeypatch):
    case = build()
    source = execute(case, decision())
    digest = Mock(wraps=bindings._record_digest)
    integrity = Mock(wraps=bindings.verify_observation_integrity)
    monkeypatch.setattr(bindings, "_record_digest", digest)
    monkeypatch.setattr(bindings, "verify_observation_integrity", integrity)

    first, _ = case.resolver._authenticated_record(source)
    second, _ = case.resolver._authenticated_record(source)
    assert first == second and first is not second
    assert integrity.call_count == 2
    assert digest.call_count <= 1

    saved = owned(case, case.resolver.export_records)
    restored = bindings.B1BindingResolver(
        session=case.session, requirements=case.requirements,
        original_context=case.context, adapters=case.adapters,
    )
    owned(case, lambda: restored.restore_records(saved))
    digest.reset_mock()
    restored._authenticated_record(source)
    digest.assert_called_once()


@pytest.mark.parametrize("limit", ["_RECORD_DIGEST_ENTRIES", "_RECORD_DIGEST_BYTES"])
def test_full_digest_cache_still_validates_new_records(build, monkeypatch, limit):
    monkeypatch.setattr(bindings, limit, 0)
    case = build()
    source = execute(case, decision())
    digest = Mock(wraps=bindings._record_digest)
    monkeypatch.setattr(bindings, "_record_digest", digest)
    first = case.resolver._authenticated_record(source)
    assert case.resolver._authenticated_record(source) == first
    assert digest.call_count == 2
    assert not case.resolver._record_digests
