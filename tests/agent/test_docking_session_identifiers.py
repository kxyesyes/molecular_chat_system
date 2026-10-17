"""URL-safe session IDs are opaque owners, never task paths or approval proof."""
import asyncio
import base64

import pytest

from src.task_runtime.docking_consent import DockingConsentError, validate_draft_identity
from src.web.agent_session import AgentSessionStore
from tests.task_runtime.test_docking_consent_draft import Rig as DraftRig, identity
from tests.agent.test_web_docking_coordinator import Rig


def issued_owner(tmp_path, monkeypatch, first_byte):
    import secrets
    original = secrets.token_urlsafe
    synthetic = base64.urlsafe_b64encode(bytes([first_byte]) + bytes(23)).decode('ascii')
    monkeypatch.setattr(secrets, 'token_urlsafe', lambda size:
        synthetic if size == 24 else original(size))
    sessions = AgentSessionStore(tmp_path / 'sessions.sqlite')
    token, owner = sessions.issue()
    assert sessions.resolve(token) == owner == synthetic
    return owner


@pytest.mark.parametrize('first_byte', [0, 248, 252])
def test_real_session_owner_can_reserve_owned_draft(tmp_path, monkeypatch, first_byte):
    owner = issued_owner(tmp_path, monkeypatch, first_byte)
    rig = DraftRig(tmp_path)
    record = rig.reserve(identity={**identity(), 'owner_session_id': owner})
    assert record['identity']['owner_session_id'] == owner
    assert rig.read(owner_session_id=owner) == record
    with pytest.raises(DockingConsentError):
        rig.read(owner_session_id='different-owner')


@pytest.mark.parametrize('first_byte', [0, 248, 252])
def test_real_session_owner_can_begin_and_observe_same_coordinator_root(
        tmp_path, monkeypatch, first_byte):
    owner = issued_owner(tmp_path, monkeypatch, first_byte)
    rig = Rig(tmp_path)
    with rig.state._connect() as connection:
        connection.execute('UPDATE agent_runs SET session_id=? WHERE trace_id=?', (owner, 'root-1'))
    rig.args['owner_session_id'] = owner
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            view = await rig.begin(coordinator)
            assert view['phase'] == 'AWAITING_INPUT', view
            assert len(rig.calls) == 1
            assert coordinator._owned('root-1', owner) is coordinator._roots['root-1']
            assert coordinator._owned('root-1', 'different-owner') is None
            refreshed = await coordinator.status(root_id='root-1', owner_session_id=owner)
            assert refreshed['task_id'] == view['task_id']
            assert refreshed['phase'] == 'AWAITING_INPUT'
            closed = await coordinator.close_root(root_id='root-1', owner_session_id=owner)
            record = rig.tasks.get_chat_docking_draft(
                preparation_id=view['preparation_id'], owner_session_id=owner)
            assert record['state'] == 'REVOKED'
            assert not closed.get('cleanup_pending')
    asyncio.run(run())


@pytest.mark.parametrize('field', ['preparation_id', 'task_id', 'trace_id', 'origin_turn_id', 'admission_revision'])
@pytest.mark.parametrize('prefix', ['-', '_'])
def test_session_support_does_not_relax_other_identity_labels(field, prefix):
    with pytest.raises(DockingConsentError):
        validate_draft_identity({**identity(), field: prefix + 'identifier'})


@pytest.mark.parametrize('value', [None, True, 1, [], {}, '', 'a' * 129,
    '../owner', '/owner', 'owner/name', r'owner\name', 'https://owner', 'owner\n'])
def test_owner_remains_native_bounded_nonpath(value):
    from src.web.docking_coordinator import DockingRequestCoordinator
    with pytest.raises(DockingConsentError):
        validate_draft_identity({**identity(), 'owner_session_id': value})
    coordinator = object.__new__(DockingRequestCoordinator)
    assert coordinator._owned('root-1', value) is None
