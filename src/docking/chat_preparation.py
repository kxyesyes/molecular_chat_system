"""Offline modeled-input construction and a caller-retained process owner.

No Web/authentication, file paths, docking adapters or scientific executables.
The synchronous builders belong in an owned child, not on an HTTP event loop.
``run_preparation(operation, inputs=..., deadline=..., owner=...)`` accepts only
ligand/inspect/pocket/receptor and the exact corresponding pure-function inputs.
``deadline`` is the coordinator's ORIGINAL absolute time.monotonic deadline
(upstream total budget 180s); this module caps each call at 60s including start,
never renews that upstream budget, and does not implement the coordinator.

The coordinator MUST retain PreparationOwner until snapshot()['settled'] is true,
even after timeout or CancelledError. The existing start helper cannot interrupt
Process.start's thread; a late start remains explicitly unsettled and is closed
by the existing helper's termination request. A logical timeout is not evidence
of physical stop. Cancellation propagates, with lifecycle facts on the owner.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import threading
import time

from src.web.process_isolation import IsolatedProcess, start_isolated_process


CHILD_MAX_SECONDS = 60.0
_INPUTS = {
    "ligand": {"smiles"},
    "inspect": {"pdb_bytes", "source"},
    "pocket": {"pdb_bytes", "source", "residue"},
    "receptor": {"pdb_bytes", "source", "protein_selection", "reference_residue", "retained_hetero_residues"},
}
_MESSAGES = {
    "LIGAND_INPUT_INVALID": "Ligand input is invalid or unsupported.",
    "LIGAND_PREPARATION_FAILED": "Modeled ligand input could not be prepared.",
    "PREPARATION_INPUT_INVALID": "Preparation request is invalid or unsupported.",
    "PREPARATION_TIMEOUT": "Input preparation exceeded its deadline.",
    "PREPARATION_FAILED": "Input preparation failed.",
    "PREPARATION_UNSETTLED": "Input preparation cleanup is not confirmed.",
}


def _failure(code):
    return dict(ok=False, code=code, message=_MESSAGES[code])


def _equivalent(reference, actual, Chem):
    """Same explicit graph/charges/isotopes plus all input-defined stereo.

    SDF atom order must stay unchanged. Newly inferred stereochemistry at input-
    unspecified centers is removed on a comparison copy, not asserted as fact.
    """
    if actual is None or reference.GetNumAtoms() != actual.GetNumAtoms():
        return False
    compared = Chem.Mol(actual)
    for a, b in zip(reference.GetAtoms(), compared.GetAtoms()):
        if (a.GetAtomicNum(), a.GetIsotope(), a.GetFormalCharge(), a.GetNumRadicalElectrons()) != (
                b.GetAtomicNum(), b.GetIsotope(), b.GetFormalCharge(), b.GetNumRadicalElectrons()):
            return False
        if a.GetChiralTag() == Chem.ChiralType.CHI_UNSPECIFIED:
            b.SetChiralTag(Chem.ChiralType.CHI_UNSPECIFIED)
    if reference.GetNumBonds() != compared.GetNumBonds():
        return False
    for bond in reference.GetBonds():
        other = compared.GetBondBetweenAtoms(bond.GetBeginAtomIdx(), bond.GetEndAtomIdx())
        if other is None or other.GetBondType() != bond.GetBondType():
            return False
        if bond.GetStereo() == Chem.BondStereo.STEREONONE:
            other.SetStereo(Chem.BondStereo.STEREONONE)
    return (Chem.GetFormalCharge(reference) == Chem.GetFormalCharge(compared)
            and Chem.MolToSmiles(Chem.RemoveHs(reference), isomericSmiles=True)
            == Chem.MolToSmiles(Chem.RemoveHs(compared), isomericSmiles=True))


def _valid_geometry(molecule):
    if molecule.GetNumConformers() != 1 or not molecule.GetConformer().Is3D():
        return False
    positions = molecule.GetConformer().GetPositions()
    if len(positions) < 2 or not all(math.isfinite(float(x)) for p in positions for x in p):
        return False
    # Planar molecules can have valid 3D coordinates. Reject collapsed atoms,
    # not physical planarity; do not invent out-of-plane coordinates.
    return all(sum(float(a-b)**2 for a, b in zip(p, q)) > 1e-8
               for i, p in enumerate(positions) for q in positions[i+1:])


def build_ligand_sdf(smiles: str) -> dict:
    """Full SMILES -> explicit-H ETKDGv3/MMFF94 SDF, not experimental pose.

    <=8192 characters, <=512 parsed atoms, one component; parseName=False,
    allowCXSMILES=False. No trimming, desalt, protonation change, fallback or
    optimizer retry. One embedding with seed 20261015; MMFF requires all
    parameters and convergence (maxIters=200, code 0). SDF read-back must retain
    graph, formal charge and input-defined stereo, and finite noncollapsed 3D.
    RDKit diagnostic logging is blocked locally to avoid leaking raw inputs.
    """
    if type(smiles) is not str or not 0 < len(smiles) <= 8192 or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in smiles):
        return _failure("LIGAND_INPUT_INVALID")
    try:
        from rdkit import Chem, rdBase
        from rdkit.Chem import AllChem

        with rdBase.BlockLogs():
            parser = Chem.SmilesParserParams()
            parser.parseName = False
            parser.allowCXSMILES = False
            parser.removeHs = False
            molecule = Chem.MolFromSmiles(smiles, parser)
            if (molecule is None or not 1 <= molecule.GetNumAtoms() <= 512
                    or len(Chem.GetMolFrags(molecule)) != 1):
                return _failure("LIGAND_INPUT_INVALID")
            molecule = Chem.AddHs(molecule)
            reference = Chem.Mol(molecule)
            params = AllChem.ETKDGv3()
            params.randomSeed = 20261015
            if AllChem.EmbedMolecule(molecule, params) != 0:
                return _failure("LIGAND_PREPARATION_FAILED")
            if not AllChem.MMFFHasAllMoleculeParams(molecule):
                return _failure("LIGAND_PREPARATION_FAILED")
            if AllChem.MMFFOptimizeMolecule(molecule, mmffVariant="MMFF94", maxIters=200) != 0:
                return _failure("LIGAND_PREPARATION_FAILED")
            if not _valid_geometry(molecule) or not _equivalent(reference, molecule, Chem):
                return _failure("LIGAND_PREPARATION_FAILED")
            block = Chem.MolToMolBlock(molecule, includeStereo=True)
            readback = Chem.MolFromMolBlock(block, sanitize=True, removeHs=False, strictParsing=True)
            if not _equivalent(reference, readback, Chem) or not _valid_geometry(readback):
                return _failure("LIGAND_PREPARATION_FAILED")
            sdf = (block.rstrip("\r\n") + "\n$$$$\n").encode("utf-8")
            return dict(ok=True, sdf_bytes=sdf, sha256=hashlib.sha256(sdf).hexdigest(),
                rule_version="modeled-ligand-v1", atom_count=readback.GetNumAtoms(),
                formal_charge=Chem.GetFormalCharge(readback),
                canonical_smiles=Chem.MolToSmiles(Chem.RemoveHs(reference), isomericSmiles=True),
                provenance=dict(input_kind="modeled_input_not_experimental_pose", rdkit_version=rdBase.rdkitVersion,
                    method="ETKDGv3", random_seed=20261015, embedding_attempts=1, explicit_hydrogens=True,
                    optimizer="MMFF94", max_iterations=200, optimizer_return_code=0))
    except Exception:
        # Scientific library exceptions can contain user content and local paths.
        return _failure("LIGAND_PREPARATION_FAILED")


def _produce(operation, inputs):
    if operation == "ligand":
        return build_ligand_sdf(**inputs)
    from .pocket_evidence import inspect_pocket_candidates, prepare_receptor_bytes, propose_pocket
    return {"inspect": inspect_pocket_candidates, "pocket": propose_pocket,
            "receptor": prepare_receptor_bytes}[operation](**inputs)


def _observe(task):
    try:
        task.result()
    except BaseException:
        pass


class _StartLease:
    """Observe physical helper calls even when their asyncio awaiter is cancelled."""

    def __init__(self, process):
        self.process = process
        self.finished = threading.Event()
        self._cleanup_lock = threading.Lock()
        self._cleanup_active = 0

    def start(self):
        try:
            self.process.start()
        finally:
            self.finished.set()

    def terminate(self):
        self._cleanup_call(self.process.terminate)

    def close(self):
        self._cleanup_call(self.process.close)

    def _cleanup_call(self, action):
        with self._cleanup_lock:
            self._cleanup_active += 1
        try:
            action()
        finally:
            with self._cleanup_lock:
                self._cleanup_active -= 1

    def cleanup_pending(self):
        with self._cleanup_lock:
            return self._cleanup_active != 0


class PreparationOwner:
    """One-shot retained lifecycle handle; keep it until snapshot says settled.

    process is inspectable for the coordinator's cleanup ownership, NOT client
    serialization. snapshot contains fixed safe booleans only; no PID or paths.
    Cancellation does not dispose this handle. Never infer settlement from a
    missing PID, is_alive=False before start completes, or a cancelled awaiter.
    """

    def __init__(self):
        self.process = None
        self._claimed = False
        self._start = None
        self._start_task = None
        self._start_stop_requested = False
        self._helper_entered = False
        self._wait_task = None
        self._wait_finished = None
        self._cleanup_task = None
        self._cleanup_ok = False

    def snapshot(self):
        start_pending = self._start is not None and not self._start.finished.is_set()
        wait_pending = self._wait_finished is not None and not self._wait_finished.is_set()
        cleanup_pending = self.process is not None and (
            self._cleanup_task is None or not self._cleanup_task.done() or not self._cleanup_ok)
        cleanup_pending = cleanup_pending or (self._start is not None and self._start.cleanup_pending())
        cleanup_pending = cleanup_pending or (self._start_task is not None and not self._start_task.done())
        try:
            stopped = self.process is None or not self.process.is_alive()
        except Exception:
            stopped = False
        return dict(settled=stopped and not (start_pending or wait_pending or cleanup_pending),
                    start_pending=start_pending, wait_pending=wait_pending, cleanup_pending=cleanup_pending)

    async def _start_process(self, deadline):
        remaining = deadline - time.monotonic()
        if self._start_stop_requested or remaining <= 0:
            # No helper/thread was dispatched, so there is no uncertain start.
            self._start.finished.set()
            if self._start_stop_requested:
                raise asyncio.CancelledError()
            raise TimeoutError()
        self._helper_entered = True
        await start_isolated_process(self._start, timeout=remaining)

    def _observe_start(self, task):
        if not self._helper_entered:
            self._start.finished.set()
        _observe(task)

    def _request_start_stop(self):
        # One cancellation enters the helper's cleanup. Repeated caller cancels
        # must not abandon its terminate/close awaiters. Physical calls are also
        # tracked by _StartLease in case shutdown cancels the helper itself.
        if self._start_task is not None and not self._start_task.done() and not self._start_stop_requested:
            self._start_stop_requested = True
            if self._helper_entered:
                self._start_task.cancel()

    def _cleanup(self):
        okay = True
        for action in (self.process.terminate, lambda: self.process.join(timeout=1.0), self.process.close):
            try:
                action()
            except Exception:
                okay = False
        self._cleanup_ok = okay

    def _wait(self, timeout):
        try:
            return self.process.wait(timeout=timeout)
        finally:
            self._wait_finished.set()


async def run_preparation(operation: str, *, inputs: dict, deadline: float, owner: PreparationOwner) -> dict:
    """Run exactly one pure producer with no reset of the supplied root deadline.

    The same deadline covers startup, work, settlement and result publication.
    Cleanup uses the existing terminate/join/close helper, retained independently
    of caller cancellation. At the deadline, stop awaiting cleanup, return only
    failure + lifecycle facts and retain owner until physical operations finish.
    Late cleanup can settle the owner, but never restore a timed-out result.
    """
    if (type(owner) is not PreparationOwner or owner._claimed or type(operation) is not str
            or operation not in _INPUTS or type(inputs) is not dict or inputs.keys() != _INPUTS[operation]
            or type(deadline) not in (int, float) or not math.isfinite(deadline)):
        return _failure("PREPARATION_INPUT_INVALID")
    owner._claimed = True
    work_deadline = min(deadline, time.monotonic() + CHILD_MAX_SECONDS)
    if work_deadline <= time.monotonic():
        return {**_failure("PREPARATION_TIMEOUT"), "lifecycle": owner.snapshot()}
    cancelled = False
    result = _failure("PREPARATION_FAILED")
    try:
        owner.process = IsolatedProcess(_produce, args=(operation, inputs))
        remaining = work_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        owner._start = _StartLease(owner.process)
        owner._start_task = asyncio.create_task(owner._start_process(work_deadline))
        owner._start_task.add_done_callback(owner._observe_start)
        done, _ = await asyncio.wait({owner._start_task}, timeout=remaining)
        if not done or time.monotonic() >= work_deadline:
            raise TimeoutError()
        owner._start_task.result()
        remaining = work_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        owner._wait_finished = threading.Event()
        owner._wait_task = asyncio.create_task(asyncio.to_thread(owner._wait, remaining))
        owner._wait_task.add_done_callback(_observe)
        done, _ = await asyncio.wait({owner._wait_task}, timeout=remaining)
        if not done or time.monotonic() >= work_deadline:
            raise TimeoutError()
        result = owner._wait_task.result()
        if type(result) is not dict or type(result.get("ok")) is not bool:
            result = _failure("PREPARATION_FAILED")
    except asyncio.CancelledError:
        cancelled = True
        owner._request_start_stop()
    except (TimeoutError, asyncio.TimeoutError):
        result = _failure("PREPARATION_TIMEOUT")
        owner._request_start_stop()
    except Exception:
        result = _failure("PREPARATION_FAILED")
    finally:
        if owner.process is not None:
            owner._cleanup_task = asyncio.create_task(asyncio.to_thread(owner._cleanup))
            owner._cleanup_task.add_done_callback(_observe)
            pending = {task for task in (owner._cleanup_task, owner._start_task, owner._wait_task)
                       if task is not None and not task.done()}
            # asyncio.wait never cancels these owned tasks. No extra drain
            # budget is appended when the original deadline has been spent.
            while pending and time.monotonic() < work_deadline:
                try:
                    _, pending = await asyncio.wait(pending, timeout=max(0.0, work_deadline-time.monotonic()))
                except asyncio.CancelledError:
                    cancelled = True
                    owner._request_start_stop()
    if cancelled:
        raise asyncio.CancelledError()
    lifecycle = owner.snapshot()
    if time.monotonic() >= work_deadline:
        result = _failure("PREPARATION_TIMEOUT")
    elif result.get("ok") and not lifecycle["settled"]:
        result = _failure("PREPARATION_UNSETTLED")
    return {**result, "lifecycle": lifecycle}
