"""Offline modeled-input and owned child contracts. No docking engine execution."""

import asyncio
import hashlib
import importlib
import importlib.util
import math
import threading
import time

import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"


def api():
    name = "src.docking.chat_preparation"
    assert importlib.util.find_spec(name) is not None, "D1 modeled input producer is missing"
    return importlib.import_module(name)


def test_real_aspirin_sdf_has_equivalent_graph_and_finite_non_degenerate_geometry():
    result = api().build_ligand_sdf(ASPIRIN)
    assert result["ok"]
    sdf = result["sdf_bytes"]
    assert sdf.endswith(b"$$$$\n")
    molecule = Chem.MolFromMolBlock(sdf.decode().split("$$$$")[0], removeHs=False, strictParsing=True)
    assert molecule is not None
    assert Chem.MolToSmiles(Chem.RemoveHs(molecule)) == Chem.MolToSmiles(Chem.MolFromSmiles(ASPIRIN))
    assert Chem.GetFormalCharge(molecule) == 0
    assert molecule.GetNumAtoms() > molecule.GetNumHeavyAtoms()
    positions = molecule.GetConformer().GetPositions()
    assert all(math.isfinite(float(x)) for row in positions for x in row)
    assert max(float(row[2]) for row in positions) - min(float(row[2]) for row in positions) > 0.01
    assert result["sha256"] == hashlib.sha256(sdf).hexdigest()
    assert result["provenance"] == dict(input_kind="modeled_input_not_experimental_pose", rdkit_version=Chem.rdBase.rdkitVersion,
        method="ETKDGv3", random_seed=20261015, embedding_attempts=1, explicit_hydrogens=True,
        optimizer="MMFF94", max_iterations=200, optimizer_return_code=0)


@pytest.mark.parametrize("smiles", ["", " CC", "CC ", "CC name", "CC\n", "CC |foo|", "C.C", "[Na+].[Cl-]", "not-smiles", "C"*513, None, True])
def test_full_smiles_rejects_names_cx_fragments_whitespace_and_atom_limit(smiles):
    result = api().build_ligand_sdf(smiles)
    assert result == {"ok":False,"code":"LIGAND_INPUT_INVALID","message":"Ligand input is invalid or unsupported."}


@pytest.mark.parametrize("seam", ["embed", "mmff_missing", "mmff_nonconverged", "changed_topology", "degenerate", "exception"])
def test_scientific_failures_do_not_fallback_or_leak(monkeypatch, seam):
    module = api()
    calls = []
    original_embed = AllChem.EmbedMolecule
    def embed(mol, params):
        calls.append((params.randomSeed, params.useRandomCoords))
        return -1 if seam == "embed" else original_embed(mol, params)
    monkeypatch.setattr(AllChem, "EmbedMolecule", embed)
    if seam == "mmff_missing": monkeypatch.setattr(AllChem, "MMFFHasAllMoleculeParams", lambda mol: False)
    if seam == "mmff_nonconverged": monkeypatch.setattr(AllChem, "MMFFOptimizeMolecule", lambda *a, **k: 1)
    if seam == "changed_topology":
        serialize = Chem.MolToMolBlock
        monkeypatch.setattr(Chem, "MolToMolBlock", lambda *a, **k: serialize(Chem.MolFromSmiles("CC")))
    if seam == "degenerate":
        def flatten(mol, **kwargs):
            for i in range(mol.GetNumAtoms()): mol.GetConformer().SetAtomPosition(i, (0,0,0))
            return 0
        monkeypatch.setattr(AllChem, "MMFFOptimizeMolecule", flatten)
    if seam == "exception":
        def fail(*a, **k): raise RuntimeError("private-path-secret-marker")
        monkeypatch.setattr(AllChem, "MMFFOptimizeMolecule", fail)
    result = module.build_ligand_sdf(ASPIRIN)
    assert not result["ok"] and "sdf_bytes" not in result
    assert result["code"] == "LIGAND_PREPARATION_FAILED"
    assert len(calls) <= 1 and all(seed == 20261015 for seed, _ in calls)
    assert "private-path-secret-marker" not in repr(result)


@pytest.mark.parametrize("smiles", ["C[C@H](O)C(=O)O", "C[NH2+]C", "F/C=C/F"])
def test_defined_stereo_and_formal_charge_survive_sdf(smiles):
    result = api().build_ligand_sdf(smiles)
    assert result["ok"]
    mol = Chem.MolFromMolBlock(result["sdf_bytes"].decode().split("$$$$")[0], removeHs=True)
    assert Chem.MolToSmiles(mol) == Chem.MolToSmiles(Chem.MolFromSmiles(smiles))


def _sleeping_child(**kwargs):
    time.sleep(10)
    return {"ok":True}


def test_owned_wrapper_real_child_success_stops_and_closes_before_return():
    module = api()
    async def run():
        owner = module.PreparationOwner()
        result = await module.run_preparation("ligand", inputs={"smiles":ASPIRIN},
            deadline=time.monotonic()+20, owner=owner)
        assert result["ok"] and owner.snapshot()["settled"] is True
        assert not owner.process.is_alive()
        assert owner.process._parent.closed and owner.process._child.closed
    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_owned_wrapper_real_child_timeout_or_cancel_is_physically_reaped(monkeypatch, cancel):
    module = api()
    actual = module.IsolatedProcess
    made = []
    class SleepingProcess(actual):
        def __init__(self, target, **kwargs):
            super().__init__(_sleeping_child)
            made.append(self)
    monkeypatch.setattr(module, "IsolatedProcess", SleepingProcess)
    async def run():
        owner = module.PreparationOwner()
        task = asyncio.create_task(module.run_preparation("ligand", inputs={"smiles":ASPIRIN},
            deadline=time.monotonic()+(10 if cancel else 0.4), owner=owner))
        if cancel:
            for _ in range(200):
                if made and made[0].is_alive(): break
                await asyncio.sleep(0.01)
            assert made and made[0].is_alive()
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        else:
            result = await task
            assert result["code"] == "PREPARATION_TIMEOUT"
            # Deadline expiry returns promptly with the owner retained. Actual
            # cleanup must still finish; the timeout must never fake settlement.
            if not owner.snapshot()["settled"]:
                assert result["lifecycle"]["settled"] is False
            for _ in range(300):
                if owner.snapshot()["settled"]: break
                await asyncio.sleep(0.005)
        assert owner.snapshot()["settled"] is True
        assert made and not made[0].is_alive()
        assert made[0]._parent.closed and made[0]._child.closed
    asyncio.run(run())


def test_start_uncertainty_remains_owned_until_late_start_finishes(monkeypatch):
    module = api()
    actual = module.IsolatedProcess
    entered, release = threading.Event(), threading.Event()
    class LateProcess(actual):
        def start(self):
            entered.set()
            release.wait(5)
            super().start()
    monkeypatch.setattr(module, "IsolatedProcess", LateProcess)
    async def run():
        owner = module.PreparationOwner()
        try:
            result = await module.run_preparation("ligand", inputs={"smiles":ASPIRIN},
                deadline=time.monotonic()+0.1, owner=owner)
            assert entered.is_set()
            assert result["code"] == "PREPARATION_TIMEOUT"
            assert owner.snapshot()["settled"] is False
            assert owner.snapshot()["start_pending"] is True
        finally:
            release.set()
        for _ in range(200):
            if owner.snapshot()["settled"]: break
            await asyncio.sleep(0.01)
        assert owner.snapshot()["settled"] and not owner.process.is_alive()
    asyncio.run(run())


def test_expired_deadline_does_not_start_or_reset_budget(monkeypatch):
    module = api()
    def forbidden(*a, **k): pytest.fail("expired budget must not construct a child")
    monkeypatch.setattr(module, "IsolatedProcess", forbidden)
    async def run():
        owner = module.PreparationOwner()
        result = await module.run_preparation("ligand", inputs={"smiles":ASPIRIN},
            deadline=time.monotonic()-1, owner=owner)
        assert result["code"] == "PREPARATION_TIMEOUT" and owner.snapshot()["settled"]
    asyncio.run(run())


def test_cancelled_wait_future_is_not_physical_wait_completion(monkeypatch):
    module = api()
    actual = module.IsolatedProcess
    entered, release = threading.Event(), threading.Event()
    class HeldWaitProcess(actual):
        def wait(self, timeout=None):
            entered.set()
            release.wait(5)
            return {"ok":True}
    monkeypatch.setattr(module, "IsolatedProcess", HeldWaitProcess)
    async def run():
        owner = module.PreparationOwner()
        task = asyncio.create_task(module.run_preparation("ligand", inputs={"smiles":ASPIRIN},
            deadline=time.monotonic()+0.2, owner=owner))
        try:
            for _ in range(200):
                if entered.is_set(): break
                await asyncio.sleep(0.005)
            assert entered.is_set()
            owner._wait_task.cancel()  # Simulates shutdown cancelling asyncio awaiters.
            with pytest.raises(asyncio.CancelledError): await task
            assert owner.snapshot()["settled"] is False
            assert owner.snapshot()["wait_pending"] is True
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        for _ in range(200):
            if owner.snapshot()["settled"]: break
            await asyncio.sleep(0.005)
        assert owner.snapshot()["settled"]
    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_start_pending_inside_actual_helper_is_reaped_after_late_spawn(monkeypatch, cancel):
    module = api()
    actual = module.IsolatedProcess
    entered, release = threading.Event(), threading.Event()
    class DelayedSpawn:
        def __init__(self, process): self.original = process
        def __getattr__(self, name): return getattr(self.original, name)
        def start(self):
            entered.set()
            release.wait(5)
            self.original.start()
    class LateProcess(actual):
        def __init__(self, target, **kwargs):
            super().__init__(_sleeping_child)
            self._process = DelayedSpawn(self._process)
    monkeypatch.setattr(module, "IsolatedProcess", LateProcess)
    async def run():
        owner = module.PreparationOwner()
        task = asyncio.create_task(module.run_preparation("ligand", inputs={"smiles":ASPIRIN},
            deadline=time.monotonic()+(10 if cancel else 0.1), owner=owner))
        try:
            if cancel:
                for _ in range(200):
                    if entered.is_set(): break
                    await asyncio.sleep(0.005)
                task.cancel()
                with pytest.raises(asyncio.CancelledError): await task
            else:
                assert (await task)["code"] == "PREPARATION_TIMEOUT"
            assert entered.is_set() and owner.snapshot()["start_pending"]
            assert not owner.snapshot()["settled"]
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        for _ in range(300):
            if owner.snapshot()["settled"]: break
            await asyncio.sleep(0.01)
        assert owner.snapshot()["settled"] and owner.process.pid is not None
        assert not owner.process.is_alive()
        assert owner.process._parent.closed and owner.process._child.closed
    asyncio.run(run())


def test_one_embed_exact_mmff_settings_and_repeatable_bytes(monkeypatch):
    module = api()
    embed, optimize = AllChem.EmbedMolecule, AllChem.MMFFOptimizeMolecule
    calls = []
    def observed_embed(mol, params):
        calls.append(("embed", params.randomSeed, params.ETversion))
        return embed(mol, params)
    def observed_optimize(mol, **kwargs):
        calls.append(("optimize", kwargs))
        return optimize(mol, **kwargs)
    monkeypatch.setattr(AllChem, "EmbedMolecule", observed_embed)
    monkeypatch.setattr(AllChem, "MMFFOptimizeMolecule", observed_optimize)
    first, second = module.build_ligand_sdf(ASPIRIN), module.build_ligand_sdf(ASPIRIN)
    assert first["ok"] and second["ok"] and first["sdf_bytes"] == second["sdf_bytes"]
    assert calls == [("embed",20261015,2), ("optimize",{"mmffVariant":"MMFF94","maxIters":200})]*2


def test_child_cap_and_spent_startup_time_are_not_reset(monkeypatch):
    module = api()
    actual = module.IsolatedProcess
    timeouts = []
    class ObservedProcess(actual):
        def start(self):
            time.sleep(0.06)
            super().start()
        def wait(self, timeout=None):
            timeouts.append(timeout)
            return super().wait(timeout=timeout)
    monkeypatch.setattr(module, "IsolatedProcess", ObservedProcess)
    async def run():
        for duration in (180, 10):
            owner = module.PreparationOwner()
            result = await module.run_preparation("ligand", inputs={"smiles":ASPIRIN},
                deadline=time.monotonic()+duration, owner=owner)
            assert result["ok"] and owner.snapshot()["settled"]
    asyncio.run(run())
    assert 58 < timeouts[0] < 59.96
    assert 8 < timeouts[1] < 9.96


def test_producers_never_invoke_legacy_or_docking_commands(monkeypatch):
    module = api()
    from src.docking.adapters.base import CommandAdapter
    from src.docking.molecular_docking_service import MolecularDockingService
    def forbidden(*a, **k): pytest.fail("D1 must not call legacy preparation or external scientific commands")
    monkeypatch.setattr(CommandAdapter, "run", forbidden)
    monkeypatch.setattr(MolecularDockingService, "_prepare_3d_molecule", forbidden)
    assert module.build_ligand_sdf(ASPIRIN)["ok"]


def test_repeated_cancellation_cannot_interrupt_owned_cleanup(monkeypatch):
    module = api()
    actual = module.IsolatedProcess
    entered, release = threading.Event(), threading.Event()
    class HeldCleanup(actual):
        def __init__(self, target, **kwargs): super().__init__(_sleeping_child)
        def terminate(self):
            entered.set()
            release.wait(5)
            super().terminate()
    monkeypatch.setattr(module, "IsolatedProcess", HeldCleanup)
    async def run():
        owner = module.PreparationOwner()
        task = asyncio.create_task(module.run_preparation("ligand", inputs={"smiles":ASPIRIN},
            deadline=time.monotonic()+10, owner=owner))
        try:
            for _ in range(200):
                if owner._wait_task is not None: break
                await asyncio.sleep(0.005)
            assert owner._wait_task is not None
            task.cancel()
            for _ in range(200):
                if entered.is_set(): break
                await asyncio.sleep(0.005)
            assert entered.is_set() and not owner.snapshot()["settled"]
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done() and not owner.snapshot()["settled"]
        finally:
            release.set()
            outcomes = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(outcomes[0], asyncio.CancelledError)
        assert owner.snapshot()["settled"] and not owner.process.is_alive()
    asyncio.run(run())


def test_cleanup_failure_cannot_publish_child_success(monkeypatch):
    module = api()
    actual = module.IsolatedProcess
    class FailedClose(actual):
        def close(self):
            super().close()
            raise RuntimeError("private-close-path-marker")
    monkeypatch.setattr(module, "IsolatedProcess", FailedClose)
    async def run():
        owner = module.PreparationOwner()
        result = await module.run_preparation("ligand", inputs={"smiles":ASPIRIN},
            deadline=time.monotonic()+10, owner=owner)
        assert not result["ok"] and result["code"] == "PREPARATION_UNSETTLED"
        assert "sdf_bytes" not in result and "private-close-path-marker" not in repr(result)
        assert not owner.snapshot()["settled"]
        assert not owner.process.is_alive() and owner.process._parent.closed
    asyncio.run(run())


def test_owned_wrapper_is_one_shot_and_rejects_non_producer_operations():
    module = api()
    async def run():
        owner = module.PreparationOwner()
        result = await module.run_preparation("vina", inputs={}, deadline=time.monotonic()+10, owner=owner)
        assert result["code"] == "PREPARATION_INPUT_INVALID" and owner.process is None
        result = await module.run_preparation("ligand", inputs={"smiles":ASPIRIN}, deadline=time.monotonic()-1, owner=owner)
        assert result["code"] == "PREPARATION_TIMEOUT"
        result = await module.run_preparation("ligand", inputs={"smiles":ASPIRIN}, deadline=time.monotonic()+10, owner=owner)
        assert result["code"] == "PREPARATION_INPUT_INVALID" and owner.process is None
    asyncio.run(run())


def test_deadline_spent_in_process_construction_never_dispatches_start(monkeypatch):
    module = api()
    actual = module.IsolatedProcess
    starts = []
    class SlowConstruction(actual):
        def __init__(self, target, **kwargs):
            super().__init__(target, **kwargs)
            time.sleep(0.05)
        def start(self):
            starts.append(True)
            super().start()
    monkeypatch.setattr(module, "IsolatedProcess", SlowConstruction)
    async def run():
        owner = module.PreparationOwner()
        result = await module.run_preparation("ligand", inputs={"smiles":ASPIRIN},
            deadline=time.monotonic()+0.02, owner=owner)
        assert result["code"] == "PREPARATION_TIMEOUT"
        await asyncio.sleep(0.03)
        assert starts == []
        assert owner.snapshot()["settled"] and owner.process._parent.closed
    asyncio.run(run())


@pytest.mark.parametrize("blocked_method", ["terminate", "close"])
def test_double_cancel_keeps_start_helper_physical_cleanup_owned(monkeypatch, blocked_method):
    module = api()
    actual = module.IsolatedProcess
    startup_entered, release_startup = threading.Event(), threading.Event()
    cleanup_entered, release_cleanup, cleanup_finished = threading.Event(), threading.Event(), threading.Event()
    call_lock, calls = threading.Lock(), {"terminate":0, "close":0}

    class HeldHelperCleanup(actual):
        def start(self):
            startup_entered.set()
            release_startup.wait(8)
            super().start()

        def held_call(self, name, function):
            with call_lock:
                calls[name] += 1
                first = calls[name] == 1
            if blocked_method == "close" and name == "terminate" and not first:
                # The owned fallback must not win the race to the first close;
                # this case specifically holds the startup helper's close.
                assert cleanup_entered.wait(8)
            if name == blocked_method and first:
                cleanup_entered.set()
                try:
                    release_cleanup.wait(8)
                    return function()
                finally:
                    cleanup_finished.set()
            return function()

        def terminate(self):
            return self.held_call("terminate", super().terminate)

        def close(self):
            return self.held_call("close", super().close)

    monkeypatch.setattr(module, "IsolatedProcess", HeldHelperCleanup)

    async def run():
        owner = module.PreparationOwner()
        task = asyncio.create_task(module.run_preparation("ligand", inputs={"smiles":ASPIRIN},
            deadline=time.monotonic()+6, owner=owner))
        try:
            for _ in range(200):
                if startup_entered.is_set(): break
                await asyncio.sleep(0.005)
            assert startup_entered.is_set()
            task.cancel()
            for _ in range(200):
                if cleanup_entered.is_set(): break
                await asyncio.sleep(0.005)
            assert cleanup_entered.is_set()
            task.cancel()
            release_startup.set()
            for _ in range(200):
                if (owner._start.finished.is_set() and owner._cleanup_task is not None
                        and owner._cleanup_task.done()): break
                await asyncio.sleep(0.005)
            assert owner._start.finished.is_set()
            assert owner._cleanup_task is not None and owner._cleanup_task.done()
            assert not cleanup_finished.is_set()
            assert owner.snapshot()["settled"] is False
            assert owner.snapshot()["cleanup_pending"] is True
        finally:
            release_startup.set()
            release_cleanup.set()
            outcomes = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(outcomes[0], asyncio.CancelledError)
        for _ in range(200):
            if owner.snapshot()["settled"]: break
            await asyncio.sleep(0.005)
        assert cleanup_finished.is_set() and owner.snapshot()["settled"]
        assert not owner.process.is_alive()
    asyncio.run(run())


@pytest.mark.parametrize("limiting_budget", ["root", "child"])
def test_cleanup_crossing_original_deadline_never_publishes_success(monkeypatch, limiting_budget):
    module = api()
    actual = module.IsolatedProcess
    entered, release, finished, produced = (threading.Event() for _ in range(4))

    class HeldClose(actual):
        def wait(self, timeout=None):
            value = super().wait(timeout=timeout)
            assert value["ok"] and "sdf_bytes" in value  # Real RDKit child, not a fake success.
            produced.set()
            return value

        def close(self):
            super().close()
            entered.set()
            try:
                release.wait(8)
            finally:
                finished.set()

    monkeypatch.setattr(module, "IsolatedProcess", HeldClose)
    if limiting_budget == "child":
        monkeypatch.setattr(module, "CHILD_MAX_SECONDS", 1.5)

    async def run():
        owner = module.PreparationOwner()
        begin = time.monotonic()
        deadline = begin + (1.5 if limiting_budget == "root" else 180)
        task = asyncio.create_task(module.run_preparation("ligand", inputs={"smiles":ASPIRIN},
            deadline=deadline, owner=owner))
        try:
            for _ in range(250):
                if entered.is_set(): break
                await asyncio.sleep(0.005)
            assert produced.is_set() and entered.is_set()
            done, _ = await asyncio.wait({task}, timeout=max(0.0, begin+1.75-time.monotonic()))
            returned_while_cleanup_blocked = bool(done)
            assert not finished.is_set() and not owner.snapshot()["settled"]
        finally:
            release.set()
            result = await task
        assert result["ok"] is False
        assert result["code"] == "PREPARATION_TIMEOUT" and "sdf_bytes" not in result
        assert returned_while_cleanup_blocked
        assert result["lifecycle"]["settled"] is False
        for _ in range(200):
            if owner.snapshot()["settled"]: break
            await asyncio.sleep(0.005)
        assert finished.is_set() and owner.snapshot()["settled"]
        assert not result["ok"]  # Late cleanup may settle ownership, never restore the result.
    asyncio.run(run())


def test_start_helper_cleanup_cannot_extend_original_deadline(monkeypatch):
    module = api()
    actual = module.IsolatedProcess
    release_start, entered, release_cleanup = threading.Event(), threading.Event(), threading.Event()
    call_lock, calls = threading.Lock(), []

    class HeldHelper(actual):
        def start(self):
            release_start.wait(8)
            super().start()

        def terminate(self):
            with call_lock:
                calls.append(True)
                first = len(calls) == 1
            if first:
                entered.set()
                release_cleanup.wait(8)
            super().terminate()

    monkeypatch.setattr(module, "IsolatedProcess", HeldHelper)

    async def run():
        owner = module.PreparationOwner()
        begin = time.monotonic()
        task = asyncio.create_task(module.run_preparation("ligand", inputs={"smiles":ASPIRIN},
            deadline=begin+0.2, owner=owner))
        try:
            for _ in range(150):
                if entered.is_set(): break
                await asyncio.sleep(0.005)
            assert entered.is_set()
            done, _ = await asyncio.wait({task}, timeout=max(0.0, begin+0.5-time.monotonic()))
            assert done, "the startup helper's cleanup extended the original deadline"
            result = task.result()
            assert result["code"] == "PREPARATION_TIMEOUT" and not owner.snapshot()["settled"]
        finally:
            release_start.set()
            release_cleanup.set()
            await asyncio.gather(task, return_exceptions=True)
        for _ in range(200):
            if owner.snapshot()["settled"]: break
            await asyncio.sleep(0.005)
        assert owner.snapshot()["settled"]
    asyncio.run(run())
