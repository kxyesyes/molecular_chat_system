"""C1 must not treat self-consistent chat metadata as source authority."""
import asyncio

import pytest

from tests.task_runtime.test_docking_consent import make_rig, prepare
from tests.task_runtime.test_chat_docking_staging import reserve_chat


def test_unowned_fixed_chat_metadata_cannot_mint_ready_consent(tmp_path, monkeypatch):
    rig = make_rig(tmp_path, monkeypatch)
    before = reserve_chat(rig)  # Self-consistent metadata, no live verified source owner.
    async def run():
        try:
            with pytest.raises(rig.api.DockingConsentError):
                await prepare(rig, before['identity'])
            row = rig.store.get_docking_consent('draft-1')
            assert row['state'] != 'READY' and row['approval_nonce_hash'] is None
            assert rig.stages == []
        finally:
            await rig.runtime.close()
    asyncio.run(run())
    assert rig.calls == []
