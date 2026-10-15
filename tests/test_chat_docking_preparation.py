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

from tests.test_receptor_projection_source import full_fixture
from tests.test_receptor_projection import selection

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
PROJECTIONS = ("projection_inspect", "projection", "projection_verify")


def projection_inputs(operation, fixture):
    raw, source = fixture
    inputs = dict(pdb_bytes=raw, source=source)
    if operation != "projection_inspect":
        inputs["selection"] = selection()
    if operation == "projection_verify":
        from src.docking.receptor_projection import project_receptor_bytes
        produced = project_receptor_bytes(**inputs)
        assert produced["ok"]
        inputs.update(derived_bytes=produced["pdb_bytes"], projection=produced["projection"])
    return inputs


def preparation_inputs(operation, fixture):
    return {"smiles": ASPIRIN} if operation == "ligand" else projection_inputs(operation, fixture)


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


@pytest.mark.parametrize("startup_delay", [0, 0.35])
def test_cancelled_wait_future_is_not_physical_wait_completion(monkeypatch, startup_delay):
    module = api()
    actual = module.IsolatedProcess
    entered, release = threading.Event(), threading.Event()
    async def run():
        loop = asyncio.get_running_loop()
        wait_entered = asyncio.Event()
        class HeldWaitProcess(actual):
            def start(self):
                time.sleep(startup_delay)
                super().start()

            def wait(self, timeout=None):
                entered.set()
                loop.call_soon_threadsafe(wait_entered.set)
                assert release.wait(15)
                return {"ok":True}
        monkeypatch.setattr(module, "IsolatedProcess", HeldWaitProcess)
        owner = module.PreparationOwner()
        task = asyncio.create_task(module.run_preparation("ligand", inputs={"smiles":ASPIRIN},
            deadline=time.monotonic()+10, owner=owner))
        try:
            # Handshake on physical thread entry, not a scheduler-sensitive
            # spin count or a 200 ms process-start deadline on a loaded host.
            await asyncio.wait_for(wait_entered.wait(), timeout=5)
            assert entered.is_set()
            owner._wait_task.cancel()  # Simulates shutdown cancelling asyncio awaiters.
            with pytest.raises(asyncio.CancelledError): await task
            assert owner.snapshot()["settled"] is False
            assert owner.snapshot()["wait_pending"] is True
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(owner._wait_finished.wait, 5)
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


@pytest.mark.parametrize("operation", ("ligand",) + PROJECTIONS)
@pytest.mark.parametrize("blocked_method", ["terminate", "close"])
def test_double_cancel_keeps_start_helper_physical_cleanup_owned(monkeypatch, blocked_method, operation, full_fixture):
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
        task = asyncio.create_task(module.run_preparation(operation, inputs=preparation_inputs(operation, full_fixture),
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


@pytest.mark.parametrize("operation", ("ligand",) + PROJECTIONS)
@pytest.mark.parametrize("limiting_budget", ["root", "child"])
def test_cleanup_crossing_original_deadline_never_publishes_success(monkeypatch, limiting_budget, operation, full_fixture):
    module = api()
    actual = module.IsolatedProcess
    entered, release, finished, produced = (threading.Event() for _ in range(4))

    class HeldClose(actual):
        def wait(self, timeout=None):
            value = super().wait(timeout=timeout)
            assert value["ok"]  # Real producer child, not a fake success.
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
        task = asyncio.create_task(module.run_preparation(operation, inputs=preparation_inputs(operation, full_fixture),
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
        assert result["code"] == "PREPARATION_TIMEOUT"
        assert set(result) == {"ok", "code", "message", "lifecycle"}
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


@pytest.mark.parametrize("operation", PROJECTIONS)
def test_projection_real_owned_child_results(full_fixture, operation):
    module = api()
    inputs = projection_inputs(operation, full_fixture)
    async def run():
        owner = module.PreparationOwner()
        result = await module.run_preparation(operation, inputs=inputs,
            deadline=time.monotonic()+20, owner=owner)
        assert result["ok"], result
        assert owner.process.pid is not None and owner.snapshot()["settled"]
        assert not owner.process.is_alive()
        assert owner.process._parent.closed and owner.process._child.closed
        assert result["source_authentication"] == "caller_responsibility"
        if operation == "projection_inspect":
            assert result["input_ready"] is False
            assert len(result["choices"]["residues"]) == 3
        elif operation == "projection":
            assert result["pdb_bytes"] != inputs["pdb_bytes"]
            assert result["projection"]["derived_input"]["atom_count"] == 5
            assert result["pocket"]["atom_count"] == 3
        else:
            import json
            canonical = json.dumps(inputs["projection"], sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False, allow_nan=False).encode()
            assert result["projection_sha256"] == hashlib.sha256(canonical).hexdigest()
    asyncio.run(run())


@pytest.mark.parametrize("operation", PROJECTIONS)
@pytest.mark.parametrize("mode", ["extra", "missing"])
def test_projection_exact_input_maps_before_constructor(monkeypatch, full_fixture, operation, mode):
    module = api()
    inputs = projection_inputs(operation, full_fixture)
    if mode == "extra": inputs["fallback"] = True
    else: inputs.pop("source")
    def forbidden(*a, **k): pytest.fail("invalid map reached process constructor")
    monkeypatch.setattr(module, "IsolatedProcess", forbidden)
    async def run():
        owner = module.PreparationOwner()
        result = await module.run_preparation(operation, inputs=inputs, deadline=time.monotonic()+10, owner=owner)
        assert result == module._failure("PREPARATION_INPUT_INVALID")
        assert owner.process is None and owner.snapshot()["settled"]
    asyncio.run(run())


class _ProjectionHook:
    def __reduce__(self):
        pytest.fail("custom serialization hook must never run")


@pytest.mark.parametrize("field,mode", [
    (field, mode)
    for field in ("pdb_bytes", "derived_bytes", "source", "selection", "projection")
    for mode in ("custom", "subclass", "cycle", "depth", "oversize")
    if not (field.endswith("bytes") and mode in {"cycle", "depth"})
])
def test_projection_preflight_rejects_unsafe_resources_before_constructor(monkeypatch, full_fixture, field, mode):
    module = api()
    inputs = projection_inputs("projection_verify", full_fixture)
    if field.endswith("bytes"):
        if mode == "custom": inputs[field] = _ProjectionHook()
        elif mode == "subclass": inputs[field] = type("Bytes", (bytes,), {})(b"x")
        else: inputs[field] = b"x" * (16 * 1024 * 1024 + 1)
    elif mode == "custom": inputs[field] = {"x": _ProjectionHook()}
    elif mode == "subclass": inputs[field] = {"x": type("List", (list,), {})([1])}
    elif mode == "cycle":
        inputs[field] = {}
        inputs[field]["x"] = inputs[field]
    elif mode == "depth":
        value = 0
        for _ in range(14): value = [value]
        inputs[field] = {"x": value}
    else: inputs[field] = {"x": [0] * 100000}
    def forbidden(*a, **k): pytest.fail("unsafe input reached process constructor/serialization")
    monkeypatch.setattr(module, "IsolatedProcess", forbidden)
    async def run():
        owner = module.PreparationOwner()
        result = await module.run_preparation("projection_verify", inputs=inputs,
            deadline=time.monotonic()+10, owner=owner)
        assert result == module._failure("PREPARATION_INPUT_INVALID")
        assert owner.process is None and owner.snapshot()["settled"]
    asyncio.run(run())


def test_projection_capture_uses_same_ledger_no_parse_deepcopy_or_serialization(monkeypatch, full_fixture):
    module = api()
    from src.docking import receptor_projection as projection, receptor_projection_source as contracts
    from src.docking import receptor_projection_pdb as pdb
    inputs = projection_inputs("projection_verify", full_fixture)
    check = getattr(module, "_check_projection_inputs", None)
    assert callable(check), "P5 parent resource capture missing"
    seen = []
    original, descriptor = contracts.check_projection_resources, projection._descriptor_resources
    def resources(value, *, budget, kind="source", _capture=False):
        seen.append((kind, value, budget))
        assert _capture is True
        return original(value, budget=budget, kind=kind, _capture=_capture)
    def descriptor_resources(value, budget, *, _capture=False):
        seen.append(("projection", value, budget))
        assert _capture is True
        return descriptor(value, budget, _capture=_capture)
    def forbidden(*a, **k): pytest.fail("capture must not parse, deepcopy, hash, or serialize")
    monkeypatch.setattr(contracts, "check_projection_resources", resources)
    monkeypatch.setattr(projection, "_descriptor_resources", descriptor_resources)
    monkeypatch.setattr(pdb, "parse_original_pdb", forbidden)
    monkeypatch.setattr(pdb, "_preflight", forbidden)
    import copy, json, pickle
    monkeypatch.setattr(copy, "deepcopy", forbidden)
    monkeypatch.setattr(json, "dumps", forbidden)
    monkeypatch.setattr(pickle, "dumps", forbidden)
    monkeypatch.setattr(hashlib, "sha256", forbidden)
    check("projection_verify", inputs)
    assert [row[0] for row in seen] == ["source", "selection", "projection"]
    assert all(value is inputs[kind] for kind, value, _ in seen)
    assert all(budget is seen[0][2] for _, _, budget in seen)
    assert 0 < seen[0][2].used < 512 * 1024 * 1024


def test_projection_preflight_aliases_charged_per_occurrence_and_aggregate_cap(monkeypatch, full_fixture):
    module = api()
    from src.docking import receptor_projection_pdb as pdb, receptor_projection_source as contracts
    check = getattr(module, "_check_projection_inputs", None)
    assert callable(check), "P5 parent no-copy resource preflight missing"
    inputs = projection_inputs("projection_verify", full_fixture)
    budgets = []
    reserve = pdb.AllocationBudget.reserve
    def observed(self, amount):
        if not budgets or budgets[-1] is not self: budgets.append(self)
        return reserve(self, amount)
    monkeypatch.setattr(pdb.AllocationBudget, "reserve", observed)
    shared = ["\x01" * 128, None, 2**63-1]
    inputs["projection"] = {"lineage": [shared]}
    check("projection_verify", inputs)
    once = budgets[-1].used
    inputs["projection"]["lineage"].append(shared)
    check("projection_verify", inputs)
    twice = budgets[-1].used
    assert twice > once
    # Each individual source/selection/descriptor fits; only their shared total fails.
    monkeypatch.setattr(contracts, "MAX_LOGICAL_BYTES", twice-1)
    with pytest.raises(pdb.ProjectionInvalid, match="^resource_limit$"):
        check("projection_verify", inputs)


def test_projection_full_512m_aggregate_rejected_before_constructor(monkeypatch, full_fixture):
    module = api()
    inputs = projection_inputs("projection_verify", full_fixture)
    # Only ~17 MiB physically allocated: aliases still count at every occurrence.
    inputs["pdb_bytes"] = inputs["derived_bytes"] = b"x" * (16 * 1024 * 1024)
    inputs["projection"] = {"lineage": [[0] * 18000] * 6}
    def forbidden(*a, **k): pytest.fail("aggregate >512 MiB reached constructor")
    monkeypatch.setattr(module, "IsolatedProcess", forbidden)
    async def run():
        owner = module.PreparationOwner()
        result = await module.run_preparation("projection_verify", inputs=inputs,
            deadline=time.monotonic()+10, owner=owner)
        assert result == module._failure("PREPARATION_INPUT_INVALID")
        assert owner.process is None
    asyncio.run(run())


@pytest.mark.parametrize("operation", PROJECTIONS)
def test_projection_root_key_thread_replacement_cannot_run_parent_hooks(monkeypatch, full_fixture, operation):
    module = api()
    inputs = projection_inputs(operation, full_fixture)
    source = inputs["source"]
    hooks, captured = [], []
    entered, replaced = threading.Event(), threading.Event()

    class Key(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            hooks.append(threading.get_ident())
            return str.__eq__(self, other)

    def mutate():
        if entered.wait(5):
            value = inputs.pop("source")
            inputs[Key("source")] = value
        replaced.set()

    original = module._check_projection_inputs

    def check(operation, incoming):
        entered.set()
        assert replaced.wait(5)
        return original(operation, incoming)

    def constructor(target, *, args):
        captured.append(args[1])
        raise RuntimeError("controlled constructor stop")

    monkeypatch.setattr(module, "_check_projection_inputs", check)
    monkeypatch.setattr(module, "IsolatedProcess", constructor)
    thread = threading.Thread(target=mutate)
    thread.start()
    try:
        async def run():
            await module.run_preparation(operation, inputs=inputs,
                deadline=time.monotonic()+10, owner=module.PreparationOwner())
        asyncio.run(run())
    finally:
        entered.set()
        thread.join(5)
    assert not thread.is_alive()
    assert hooks == []
    assert len(captured) == 1 and captured[0]["source"] == source
    assert captured[0]["source"] is not source


@pytest.mark.parametrize("operation", PROJECTIONS)
def test_projection_deadline_covers_preflight_before_constructor(monkeypatch, full_fixture, operation):
    module = api()
    from src.docking import receptor_projection_source as contracts
    inputs = projection_inputs(operation, full_fixture)
    original = contracts.check_projection_resources
    def delayed(*a, **k):
        time.sleep(0.04)
        return original(*a, **k)
    def forbidden(*a, **k): pytest.fail("spent preflight deadline reached constructor")
    monkeypatch.setattr(contracts, "check_projection_resources", delayed)
    monkeypatch.setattr(module, "IsolatedProcess", forbidden)
    async def run():
        owner = module.PreparationOwner()
        result = await module.run_preparation(operation, inputs=inputs,
            deadline=time.monotonic()+0.02, owner=owner)
        assert result["code"] == "PREPARATION_TIMEOUT"
        assert owner.process is None and owner.snapshot()["settled"]
    asyncio.run(run())


@pytest.mark.parametrize("operation", PROJECTIONS)
@pytest.mark.parametrize("phase", ["before_spawn", "after_spawn", "late_result"])
def test_projection_cancel_and_late_real_result_retains_owner(monkeypatch, full_fixture, operation, phase):
    module = api()
    inputs = projection_inputs(operation, full_fixture)
    actual = module.IsolatedProcess
    entered, release = threading.Event(), threading.Event()
    class DelayedSpawn:
        def __init__(self, process): self.original = process
        def __getattr__(self, name): return getattr(self.original, name)
        def start(self):
            entered.set()
            assert release.wait(8)
            self.original.start()
    class Held(actual):
        def __init__(self, target, **kwargs):
            super().__init__(target, **kwargs)
            if phase == "before_spawn": self._process = DelayedSpawn(self._process)
        def wait(self, timeout=None):
            value = super().wait(timeout=timeout)
            assert value["ok"]  # Preserve the actual producer result, including late delivery.
            entered.set()
            assert release.wait(8)
            return value
    monkeypatch.setattr(module, "IsolatedProcess", Held)
    async def run():
        owner = module.PreparationOwner()
        task = asyncio.create_task(module.run_preparation(operation, inputs=inputs,
            deadline=time.monotonic()+(0.8 if phase == "before_spawn" else 2), owner=owner))
        try:
            for _ in range(600):
                if entered.is_set() or task.done(): break
                await asyncio.sleep(0.005)
            assert entered.is_set()
            if phase != "before_spawn": assert owner.process.pid is not None
            if phase != "late_result":
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                with pytest.raises(asyncio.CancelledError): await task
            else:
                result = await task
                assert result["code"] == "PREPARATION_TIMEOUT"
                assert set(result) == {"ok", "code", "message", "lifecycle"}
            assert not owner.snapshot()["settled"]
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        for _ in range(600):
            if owner.snapshot()["settled"]: break
            await asyncio.sleep(0.005)
        assert owner.snapshot()["settled"] and owner.process.pid is not None
        assert not owner.process.is_alive()
    asyncio.run(run())


@pytest.mark.parametrize("operation", PROJECTIONS)
def test_projection_dispatch_never_executes_scientific_or_legacy_tools(monkeypatch, full_fixture, operation):
    module = api()
    inputs = projection_inputs(operation, full_fixture)
    from src.docking.adapters.base import CommandAdapter
    from src.docking.molecular_docking_service import MolecularDockingService
    from src.docking import pocket_evidence
    import os, subprocess
    def forbidden(*a, **k): pytest.fail("projection must not execute scientific/legacy preparation")
    monkeypatch.setattr(CommandAdapter, "run", forbidden)
    monkeypatch.setattr(MolecularDockingService, "_prepare_3d_molecule", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    for name in ("inspect_pocket_candidates", "prepare_receptor_bytes", "propose_pocket"):
        monkeypatch.setattr(pocket_evidence, name, forbidden)
    assert module._produce(operation, inputs)["ok"]


@pytest.mark.parametrize("field", ["source", "selection", "projection"])
@pytest.mark.parametrize("value", [True, 1.5, 2**63, "x" * 129, "\ud800", (1,), b"x"])
def test_projection_resource_scalars_fail_before_constructor(monkeypatch, full_fixture, field, value):
    module = api()
    inputs = projection_inputs("projection_verify", full_fixture)
    inputs[field] = {"x": value}
    def forbidden(*a, **k): pytest.fail("invalid scalar reached constructor")
    monkeypatch.setattr(module, "IsolatedProcess", forbidden)
    async def run():
        result = await module.run_preparation("projection_verify", inputs=inputs,
            deadline=time.monotonic()+10, owner=module.PreparationOwner())
        assert result == module._failure("PREPARATION_INPUT_INVALID")
    asyncio.run(run())


@pytest.mark.parametrize("ceiling", ["source_json", "selection_json", "projection_json", "nodes"])
def test_projection_resource_caps_fail_before_constructor(monkeypatch, full_fixture, ceiling):
    module = api()
    from src.docking import receptor_projection_source as contracts, receptor_projection as projection
    inputs = projection_inputs("projection_verify", full_fixture)
    if ceiling == "projection_json": monkeypatch.setattr(projection, "MAX_PROJECTION_JSON_BYTES", 1)
    else:
        name = {"source_json": "MAX_SOURCE_JSON_BYTES", "selection_json": "MAX_SELECTION_JSON_BYTES",
                "nodes": "MAX_NODES"}[ceiling]
        monkeypatch.setattr(contracts, name, 1)
    def forbidden(*a, **k): pytest.fail("resource ceiling exceeded before constructor")
    monkeypatch.setattr(module, "IsolatedProcess", forbidden)
    async def run():
        result = await module.run_preparation("projection_verify", inputs=inputs,
            deadline=time.monotonic()+10, owner=module.PreparationOwner())
        assert result == module._failure("PREPARATION_INPUT_INVALID")
    asyncio.run(run())


def test_projection_outer_map_rejects_keys_without_custom_hooks(monkeypatch, full_fixture):
    module = api()
    class Key(str):
        def __hash__(self): return hash("source")
        def __eq__(self, other): pytest.fail("input-map equality invoked a custom hook")
    raw, source = full_fixture
    inputs = {"pdb_bytes": raw, Key("source"): source}
    def forbidden(*a, **k): pytest.fail("custom key reached constructor")
    monkeypatch.setattr(module, "IsolatedProcess", forbidden)
    async def run():
        result = await module.run_preparation("projection_inspect", inputs=inputs,
            deadline=time.monotonic()+10, owner=module.PreparationOwner())
        assert result == module._failure("PREPARATION_INPUT_INVALID")
    asyncio.run(run())


@pytest.mark.parametrize("operation", PROJECTIONS)
def test_projection_child_semantic_rejections_are_fixed_no_raw_diagnostics(full_fixture, operation):
    module = api()
    inputs = projection_inputs(operation, full_fixture)
    inputs["source"]["provenance"]["record_id"] = "private-path-secret-marker"
    async def run():
        owner = module.PreparationOwner()
        result = await module.run_preparation(operation, inputs=inputs,
            deadline=time.monotonic()+20, owner=owner)
        assert result == dict(ok=False, code="RECEPTOR_PROJECTION_INVALID", reason="source_invalid",
            message="Receptor projection input is invalid.", lifecycle=owner.snapshot())
        assert owner.process.pid is not None and owner.snapshot()["settled"]
        assert "private-path-secret-marker" not in repr(result)
    asyncio.run(run())


def _native_type_impostor(hooks, *, base=object):
    """Harmless adversary: tuple type-membership can call reflected metaclass eq."""
    class NativePretender(type):
        def __eq__(cls, other):
            hooks.append("metaclass.__eq__")
            return True
        __hash__ = type.__hash__

    class Impostor(base, metaclass=NativePretender):
        def __len__(self):
            hooks.append("__len__")
            return 0
        def __iter__(self):
            hooks.append("__iter__")
            return iter(())
        def __reduce__(self):
            hooks.append("__reduce__")
            return list, ()
        def __reduce_ex__(self, protocol):
            hooks.append("__reduce_ex__")
            return self.__reduce__()
        def __float__(self):
            hooks.append("__float__")
            return time.monotonic()+20

    return Impostor(time.monotonic()+20) if base is float else Impostor()


def _impostor_location(impostor, location):
    if location == "root": return impostor
    if location == "dict_value": return {"x": impostor}
    if location == "list_value": return {"x": [{"nested": [impostor]}]}
    return {impostor: 1}  # Native dictionary must reject custom keys without hooks.


@pytest.mark.parametrize("operation,field", [
    ("projection_inspect", "source"),
    ("projection", "source"), ("projection", "selection"),
    ("projection_verify", "source"), ("projection_verify", "selection"),
    ("projection_verify", "projection"),
])
@pytest.mark.parametrize("location", ["dict_value", "list_value", "dict_key"])
def test_projection_metaclass_admission_rejects_without_hooks_or_constructor(
        monkeypatch, full_fixture, operation, field, location):
    module = api()
    inputs = projection_inputs(operation, full_fixture)
    hooks, constructed = [], []
    inputs[field] = _impostor_location(_native_type_impostor(hooks), location)
    def forbidden(*a, **k):
        constructed.append(True)
        raise RuntimeError("constructor must not run")
    monkeypatch.setattr(module, "IsolatedProcess", forbidden)
    async def run():
        owner = module.PreparationOwner()
        result = await module.run_preparation(operation, inputs=inputs,
            deadline=time.monotonic()+20, owner=owner)
        assert hooks == [], "resource admission invoked untrusted hooks"
        assert constructed == []
        assert result == module._failure("PREPARATION_INPUT_INVALID")
        assert owner.process is None and owner.snapshot()["settled"]
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["source", "selection", "projection"])
@pytest.mark.parametrize("location", ["root", "dict_value", "list_value", "dict_key"])
def test_projection_metaclass_direct_walks_reject_without_hooks(kind, location):
    from src.docking import receptor_projection as projection, receptor_projection_source as contracts
    from src.docking.receptor_projection_pdb import AllocationBudget, ProjectionInvalid
    hooks = []
    value = _impostor_location(_native_type_impostor(hooks), location)
    rejected = False
    try:
        if kind == "projection": projection._descriptor_resources(value, AllocationBudget())
        else: contracts.check_projection_resources(value, budget=AllocationBudget(), kind=kind)
    except ProjectionInvalid:
        rejected = True
    assert hooks == [], "direct resource walker invoked untrusted hooks"
    assert rejected


@pytest.mark.parametrize("operation", PROJECTIONS)
def test_projection_metaclass_deadline_rejected_without_hooks_or_constructor(monkeypatch, full_fixture, operation):
    module = api()
    inputs = projection_inputs(operation, full_fixture)
    hooks, constructed = [], []
    deadline = _native_type_impostor(hooks, base=float)
    def forbidden(*a, **k):
        constructed.append(True)
        raise RuntimeError("constructor must not run")
    monkeypatch.setattr(module, "IsolatedProcess", forbidden)
    async def run():
        owner = module.PreparationOwner()
        result = await module.run_preparation(operation, inputs=inputs, deadline=deadline, owner=owner)
        assert hooks == [], "deadline admission invoked untrusted hooks"
        assert constructed == []
        assert result == module._failure("PREPARATION_INPUT_INVALID")
        assert owner.process is None and owner.snapshot()["settled"]
    asyncio.run(run())


@pytest.mark.parametrize("operation,field", [
    ("projection_inspect", "source"),
    ("projection", "source"), ("projection", "selection"),
    ("projection_verify", "source"), ("projection_verify", "selection"),
    ("projection_verify", "projection"),
])
@pytest.mark.parametrize("mutation", ["root_hook", "nested_hook", "nested_native"])
def test_projection_capture_prevents_caller_alias_mutation_before_spawn(full_fixture, operation, field, mutation):
    module = api()
    inputs = projection_inputs(operation, full_fixture)
    expected = module._produce(operation, inputs)
    assert expected["ok"]
    hooks = []
    class Marker:
        def __init__(self, original): self.original = original
        def __reduce__(self):
            hooks.append("parent.__reduce__")
            return type(self.original), (self.original,)
    if field == "source":
        alias, key = inputs[field]["provenance"], "proof_id"
    elif field == "selection":
        alias, key = inputs[field]["conformer_choices"][0], "altloc"
    else:
        alias, key = inputs[field]["lineage"][0], "reason"
    original = alias[key]
    async def run():
        owner = module.PreparationOwner()
        task = asyncio.create_task(module.run_preparation(operation, inputs=inputs,
            deadline=time.monotonic()+20, owner=owner))
        # Deterministic real scheduling seam: constructor/preflight have run;
        # the separately scheduled start task has not. No production monkeypatch.
        await asyncio.sleep(0)
        try:
            assert owner.process is not None and owner.process.pid is None
            if mutation == "root_hook": inputs[field] = Marker(inputs[field])
            elif mutation == "nested_hook": alias[key] = Marker(original)
            else: alias[key] = "caller-changed-after-preflight"
        finally:
            result = await task
        assert hooks == []
        assert owner.process._args[1] is not inputs
        assert {k: v for k, v in result.items() if k != "lifecycle"} == expected
        captured = owner.process._args[1][field]
        if field == "source": captured_alias = captured["provenance"]
        elif field == "selection": captured_alias = captured["conformer_choices"][0]
        else: captured_alias = captured["lineage"][0]
        assert captured_alias is not alias and captured_alias[key] == original
        assert owner.snapshot()["settled"] and not owner.process.is_alive()
        assert owner.process._parent.closed and owner.process._child.closed
    asyncio.run(run())


@pytest.mark.parametrize("operation,field", [
    ("projection_inspect", "pdb_bytes"), ("projection", "pdb_bytes"),
    ("projection_verify", "pdb_bytes"), ("projection_verify", "derived_bytes"),
])
def test_projection_capture_keeps_bounded_original_bytes_after_caller_replacement(full_fixture, operation, field):
    module = api()
    inputs = projection_inputs(operation, full_fixture)
    original = inputs[field]
    oversized = b"x" * (16 * 1024 * 1024 + 1)
    async def run():
        owner = module.PreparationOwner()
        task = asyncio.create_task(module.run_preparation(operation, inputs=inputs,
            deadline=time.monotonic()+20, owner=owner))
        await asyncio.sleep(0)
        try:
            assert owner.process is not None and owner.process.pid is None
            inputs[field] = oversized
        finally:
            result = await task
        assert result["ok"], result
        assert owner.process._args[1][field] is original  # Native bytes are immutable.
        assert owner.snapshot()["settled"] and not owner.process.is_alive()
        assert owner.process._parent.closed and owner.process._child.closed
    asyncio.run(run())


def test_projection_capture_detaches_every_mutable_container_and_duplicate_alias(full_fixture):
    module = api()
    inputs = projection_inputs("projection_verify", full_fixture)
    shared = ["safe", None]
    inputs["projection"] = {"lineage": [shared, shared]}
    captured = module._check_projection_inputs("projection_verify", inputs)
    assert type(captured) is dict, "preflight must return a captured payload, not caller aliases"
    def containers(value):
        if type(value) is dict:
            return {id(value)}.union(*(containers(child) for child in value.values()))
        if type(value) is list:
            return {id(value)}.union(*(containers(child) for child in value))
        return set()
    assert not containers(captured).intersection(containers(inputs))
    assert captured == inputs
    assert captured["projection"]["lineage"][0] is not captured["projection"]["lineage"][1]


@pytest.mark.parametrize("kind", ["source", "selection", "projection"])
def test_projection_capture_charges_detached_containers_on_supplied_ledger(kind):
    from src.docking import receptor_projection as projection, receptor_projection_source as contracts
    from src.docking.receptor_projection_pdb import AllocationBudget, ProjectionInvalid
    shared = ["\\\"\n\u0000é中😀", -123]
    value = {"x": [shared, shared]}
    def walk(budget, capture):
        if kind == "projection":
            return projection._descriptor_resources(value, budget, _capture=capture)
        return contracts.check_projection_resources(value, budget=budget, kind=kind, _capture=capture)
    original_budget, capture_budget = AllocationBudget(), AllocationBudget()
    original = walk(original_budget, False)
    captured = walk(capture_budget, True)
    if kind != "projection":
        facts, captured = captured
        assert facts == original  # Captured bytes/nodes retain the existing resource rules.
    assert captured == value and captured is not value
    assert captured["x"][0] is not shared and captured["x"][0] is not captured["x"][1]
    assert capture_budget.used > original_budget.used
    with pytest.raises(ProjectionInvalid, match="^resource_limit$"):
        walk(AllocationBudget(capture_budget.used-1), True)
