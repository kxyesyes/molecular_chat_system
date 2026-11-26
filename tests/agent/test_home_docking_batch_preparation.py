import asyncio
import hashlib
import json

import pytest

from src.docking import chat_preparation, source_preparation
from src.task_runtime.database import connection
from src.task_runtime.runtime import TaskRuntime
from src.task_runtime.docking_consent import DockingConsentError
from src.web.docking_batch_consent import batch_approve, batch_preview
from src.web.docking_view import capture_docking_view
from tests.agent.test_web_docking_consent import target_case
from tests.agent.test_web_docking_sources import source_rig
from tests.agent.test_web_docking_selection import _complete_source_child, select
from tests.agent.test_web_docking_parameters import confirmation_args


def test_preflight_single_survivor_keeps_ordinal_and_exact_sdf(tmp_path, target_case, monkeypatch):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, "_source_candidate", _complete_source_child)
    prepared = chat_preparation.build_ligand_sdf("OCC")
    original = chat_preparation.run_preparation
    async def receptor_only(operation, **kwargs):
        assert operation != "ligand", "a passed preflight must reuse the same SDF"
        return await original(operation, **kwargs)
    monkeypatch.setattr(chat_preparation, "run_preparation", receptor_only)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            rows = [dict(ordinal=2, smiles="CCO")]
            skipped = [dict(ordinal=1, reason="未通过结构检查")]
            initial = await rig.begin(coordinator, structure_context=pointer,
                batch=dict(pdb_id="1ABC", ligands=rows), prepared={2: prepared}, skipped=skipped)
            assert initial["batch"]["skipped"] == skipped
            result = await select(coordinator, initial)
            assert result["reason_code"] == "parameters_confirmation_required"
            root = coordinator._owned("root-1", "owner-session")
            assert root.batch_ligands[0]["sdf_bytes"] == prepared["sdf_bytes"]
            await coordinator.confirm_parameters(**confirmation_args(result))
            proof = await batch_preview(coordinator, root, root.accepted_revision)
            assert proof["ligands"] == rows and not root.batch_submitting
            await coordinator.close_root(root_id="root-1", owner_session_id="owner-session")
    asyncio.run(run())


def test_batch_selection_prepares_one_receptor_and_every_ligand_once(tmp_path, target_case, monkeypatch):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, "_source_candidate", _complete_source_child)
    calls = []
    original = chat_preparation.run_preparation

    async def observed(operation, **kwargs):
        calls.append((operation, kwargs["inputs"].get("smiles")))
        return await original(operation, **kwargs)

    monkeypatch.setattr(chat_preparation, "run_preparation", observed)
    rows = [{"ordinal": 3, "smiles": "CCO"}, {"ordinal": 1, "smiles": "CCN"}]
    batch = {"pdb_id": "1ABC", "ligands": rows}

    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            args = dict(root_id="root-1", owner_session_id="owner-session")
            for changed in (dict(batch, pdb_id="2XYZ"),
                            dict(batch, ligands=[rows[1], rows[0]]),
                            dict(batch, ligands=[rows[0], rows[0]])):
                invalid = await rig.begin(coordinator, structure_context=pointer, batch=changed)
                assert invalid["reason_code"] == "invalid_input"
                assert not coordinator._roots
            initial = await rig.begin(coordinator, structure_context=pointer, batch=batch)
            assert initial["batch"] == dict(batch, task_id=initial["task_id"])
            root = coordinator._owned("root-1", "owner-session")
            assert root.batch_proof is None and not root.batch_submitting
            prepared = await select(coordinator, initial)
            assert prepared["reason_code"] == "parameters_confirmation_required", prepared
            assert calls == [("projection_inspect", None), ("projection", None), ("ligand", "CCO"), ("ligand", "CCN")]
            assert [(item["ordinal"], item["canonical_smiles"]) for item in root.batch_ligands] == [(3, "CCO"), (1, "CCN")]
            assert all(hashlib.sha256(item["sdf_bytes"]).hexdigest() == item["sha256"] for item in root.batch_ligands)
            await select(coordinator, initial)
            assert len(calls) == 4
            event = await capture_docking_view(coordinator, **args, origin_turn_id=root.origin,
                                               sequence=1, send_generation=1)
            assert event["batch"] == dict(batch, task_id=root.task_id)
            assert "sdf_bytes" not in json.dumps(event)
            assert root.batch is not batch
            initial["batch"]["ligands"][0]["smiles"] = "C"
            assert root.batch == batch
            confirmed = await coordinator.confirm_parameters(**confirmation_args(prepared))
            assert confirmed["reason_code"] == "inputs_confirmed_staging_required"
            stage_args = confirmation_args(confirmed)
            del stage_args["parameters"]
            runtime = object.__new__(TaskRuntime)
            runtime.store = rig.tasks
            assert (await coordinator.prepare_inputs(**stage_args, runtime=runtime))["phase"] == "UNAVAILABLE"
            assert (await coordinator.approve_inputs(**args, approval_nonce="ignored",
                                                      binding_digest="ignored", confirm=True))["phase"] == "UNAVAILABLE"
            assert root.input_context is None and not root.staging_started
            proof = await batch_preview(coordinator, root, root.accepted_revision)
            assert proof["ligands"] == rows
            assert proof["receptor_sha256"] == prepared["prepared_preview"]["derived_sha256"]
            assert root.batch_proof and proof["approval_nonce"] == root.batch_proof[1]
            approval = {key: proof[key] for key in ("approval_nonce", "binding_digest")}
            approval["confirm"] = True
            submissions = []

            class RecordingExecution:
                async def submit(self, **data):
                    with data.pop("admission_guard")():
                        submissions.append(data)
                    return {"task_id": data["task_id"]}

            execution = RecordingExecution()
            with pytest.raises(ValueError):
                await batch_approve(coordinator, root, execution, dict(approval, approval_nonce="0"*64),
                                    check_request=lambda: None)
            original_sdf = root.batch_ligands[1]["sdf_bytes"]
            root.batch_ligands[1]["sdf_bytes"] += b"x"
            async with coordinator._gate.request():
                with pytest.raises(DockingConsentError), root.input_context._source_guard():
                    pass
            with pytest.raises(ValueError):
                await batch_approve(coordinator, root, execution, approval, check_request=lambda: None)
            root.batch_ligands[1]["sdf_bytes"] = original_sdf
            assert not submissions
            with connection(rig.tasks.db_path) as conn:
                assert conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"] == 0
            result = await batch_approve(coordinator, root, execution, approval, check_request=lambda: None)
            assert result["task_id"] == root.task_id
            with pytest.raises(ValueError):
                await batch_approve(coordinator, root, execution, approval, check_request=lambda: None)
            assert len(submissions) == 1
            actual = submissions[0]["ligands"]
            assert [{k: row[k] for k in ("ordinal", "smiles")} for row in actual] == rows
            assert actual[1]["sdf_bytes"] == original_sdf
            assert not root.batch_ligands and root.batch is None
    asyncio.run(run())
