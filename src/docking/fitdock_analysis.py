"""Template-guided pose comparison in the query receptor's coordinate frame."""

import hashlib
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile

from rdkit import Chem
from rdkit.Chem import rdMolAlign
from scipy.stats import rankdata

from .adapters.base import CommandAdapter
from .pose_export import pose_sdf_from_pdbqt


def fitdock_binary():
    binary = shutil.which(os.environ.get("MEDCHAT_FITDOCK_BIN") or "FitDock")
    return str(Path(binary).resolve()) if binary else None


def obabel_binary():
    bundled = Path(sys.prefix) / "Library/bin/obabel.exe"
    return str(bundled) if bundled.is_file() else shutil.which("obabel")


def rank_agreement(poses, reference):
    """Mean of Vina energy rank and symmetry-aware, unaligned heavy-atom RMSD rank."""
    identity = Chem.MolToSmiles(reference)
    rows = []
    for number, energy, mol in poses:
        if mol is None or Chem.MolToSmiles(mol) != identity:
            raise ValueError("FitDock 姿势与 Vina 配体的分子图或立体化学不一致")
        rmsd = rdMolAlign.CalcRMS(mol, reference, maxMatches=10000)
        if not math.isfinite(rmsd):
            raise ValueError("姿势 RMSD 无效")
        rows.append({"pose": number, "vina_energy": energy, "rmsd": rmsd})
    energies = rankdata([row["vina_energy"] for row in rows])
    distances = rankdata([row["rmsd"] for row in rows])
    for row, energy_rank, distance_rank in zip(rows, energies, distances):
        row.update(vina_rank=float(energy_rank), rmsd_rank=float(distance_rank),
                   mean_rank=float((energy_rank + distance_rank) / 2))
    return sorted(rows, key=lambda row: (row["mean_rank"], row["vina_energy"], row["pose"]))


def compare_with_fitdock(vina_text, structure, mode, receptor_path):
    from .molecular_docking_service import MolecularDockingService

    scores = MolecularDockingService.parse_vina_results_text(vina_text)
    if not scores:
        raise ValueError("缺少已验证的 Vina 姿势")
    poses = [(r["pose"], r["binding_energy"],
              Chem.MolFromMolBlock(pose_sdf_from_pdbqt(vina_text, r["pose"]))) for r in scores]
    if any(mol is None for _, _, mol in poses):
        raise ValueError("无法重建 Vina 配体")
    source = structure.decode("utf-8")
    provenance = {"mode": mode, "input_sha256": hashlib.sha256(structure).hexdigest(),
                  "vina_sha256": hashlib.sha256(vina_text.encode()).hexdigest(),
                  "ranking": "mean-rank-vina-fixed-frame-rmsd-v1"}
    if mode == "template":
        binary = fitdock_binary()
        if not binary:
            raise FileNotFoundError("未配置 FitDock 可执行文件")
        if Path(receptor_path).suffix.lower() != ".pdb":
            raise ValueError("本地 FitDock 需要 PDB 格式受体")
        template = Chem.MolFromMol2Block(source) if source.count("@<TRIPOS>MOLECULE") == 1 else None
        if template is None or not template.GetConformer().Is3D():
            raise ValueError("模板需要单个带三维坐标的 MOL2 配体")
        with tempfile.TemporaryDirectory(prefix="medchat_fitdock_") as directory:
            work = Path(directory)
            # The user supplies a template ligand already aligned to this receptor.
            (work / "template.mol2").write_text(source, encoding="utf-8")
            protein = Path(receptor_path).read_bytes()
            # Keep structural metals in the receptor, excluding water and deposited ligands.
            first_model = protein.split(b"ENDMDL", 1)[0]
            metal_types = {b"ZN", b"MG", b"MN", b"FE", b"CA", b"CO", b"NI", b"CU"}
            atoms = [line for line in first_model.splitlines() if line.startswith(b"ATOM  ") or
                     (line.startswith(b"HETATM") and line[76:78].strip().upper() in metal_types)]
            if not any(line.startswith(b"ATOM  ") for line in atoms):
                raise ValueError("FitDock 受体缺少蛋白质原子")
            (work / "protein.pdb").write_bytes(b"\n".join(atoms) + b"\nEND\n")
            obabel = obabel_binary()
            if not obabel:
                raise FileNotFoundError("未配置 Open Babel")
            (work / "query.sdf").write_text(Chem.MolToMolBlock(poses[0][2]), encoding="utf-8")
            conversion = CommandAdapter(obabel).run(
                [obabel, "query.sdf", "-O", "query.mol2", "-h"], cwd=str(work), timeout=30)
            if conversion.returncode or not (work / "query.mol2").is_file():
                raise RuntimeError("Open Babel 未能生成查询配体 MOL2")
            command = [binary, "-Tprot", "protein.pdb", "-Tlig", "template.mol2",
                       "-Qprot", "protein.pdb", "-Qlig", "query.mol2", "-ot", "aligned.mol2",
                       "-os", "sampled.mol2", "-o", "fit.mol2"]
            result = CommandAdapter(binary).run(command, cwd=str(work), timeout=180)
            output = work / "fit.mol2"
            if result.returncode or not output.is_file():
                raise RuntimeError(f"FitDock 未生成有效姿势（退出码 {result.returncode}）")
            source = output.read_text(encoding="utf-8")
            provenance["executable_sha256"] = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
    if "@<TRIPOS>MOLECULE" in source:
        reference = Chem.MolFromMol2Block(source) if source.count("@<TRIPOS>MOLECULE") == 1 else None
    else:
        blocks = [block for block in source.split("$$$$") if block.strip()]
        reference = Chem.MolFromMolBlock(blocks[0]) if len(blocks) == 1 else None
    if reference is None or not reference.GetNumConformers() or not reference.GetConformer().Is3D():
        raise ValueError("请选择单个三维 FitDock 姿势（MOL2 或 SDF）")
    rows = rank_agreement(poses, reference)
    reference_sdf = Chem.MolToMolBlock(reference)
    provenance["reference_sdf_sha256"] = hashlib.sha256(reference_sdf.encode()).hexdigest()
    return {"rows": rows, "fitdock_sdf": reference_sdf, "provenance": provenance}
