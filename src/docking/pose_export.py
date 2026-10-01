"""Strict conversion of a validated Vina pose to an SDF artifact."""

from __future__ import annotations

import math
import re
from typing import Any


class PoseExportError(ValueError):
    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _extract_pose_atom_lines(pdbqt_text: str, pose_index: int) -> list[str]:
    if type(pose_index) is not int or pose_index <= 0:
        raise PoseExportError("pose index must be a positive integer", 422)
    current = 0
    collecting = False
    atom_lines: list[str] = []
    for line in pdbqt_text.splitlines():
        if line.startswith("MODEL"):
            current += 1
            collecting = current == pose_index
            continue
        if line.startswith("ENDMDL"):
            if collecting:
                break
            collecting = False
            continue
        if collecting and line.startswith(("ATOM", "HETATM")):
            atom_lines.append(line)
    return atom_lines


def _extract_remark_smiles(pdbqt_text: str) -> str:
    for line in pdbqt_text.splitlines():
        if line.startswith("REMARK SMILES ") and not line.startswith("REMARK SMILES IDX"):
            return line.split("REMARK SMILES ", 1)[1].strip()
    return ""


def _extract_remark_pairs(pdbqt_text: str, prefix: str) -> list[tuple[int, int]]:
    nums: list[int] = []
    for line in pdbqt_text.splitlines():
        if line.startswith("MODEL"):
            if nums:
                break
            continue
        if line == "ENDMDL" and nums:
            break
        if line.startswith(prefix):
            nums.extend(int(value) for value in re.findall(r"\d+", line[len(prefix):]))
    return [(nums[index], nums[index + 1]) for index in range(0, len(nums) - 1, 2)]


def pose_sdf_from_pdbqt(
    pdbqt_text: str,
    pose_index: int = 1,
    *,
    keep_hydrogens: bool = False,
) -> str:
    smiles = _extract_remark_smiles(pdbqt_text)
    if not smiles:
        raise PoseExportError("结果文件缺少 SMILES 注释，无法重建标准配体结构")
    smiles_idx_pairs = _extract_remark_pairs(pdbqt_text, "REMARK SMILES IDX")
    if not smiles_idx_pairs:
        raise PoseExportError("结果文件缺少 SMILES IDX 映射，无法重建标准配体结构")
    h_parent_pairs = _extract_remark_pairs(pdbqt_text, "REMARK H PARENT")
    atom_lines = _extract_pose_atom_lines(pdbqt_text, pose_index)
    if not atom_lines:
        raise PoseExportError(f"未找到 Pose {pose_index} 的坐标", 404)

    try:
        from rdkit import Chem
        from rdkit.Geometry import Point3D
    except ImportError as exc:
        raise PoseExportError("RDKit 不可用，无法导出标准 SDF", 503) from exc

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise PoseExportError("无法从结果文件中的 SMILES 重建分子")
    mol = Chem.AddHs(mol)
    conf = Chem.Conformer(mol.GetNumAtoms())
    mol.RemoveAllConformers()
    mol.AddConformer(conf, assignId=True)
    pose_coords: dict[int, tuple[float, float, float]] = {}
    seen_serials: set[int] = set()
    for line in atom_lines:
        try:
            serial = int(line[6:11])
            coords = tuple(float(line[start:end]) for start, end in ((30, 38), (38, 46), (46, 54)))
        except (TypeError, ValueError, IndexError) as exc:
            raise PoseExportError("Pose 含有无效原子坐标") from exc
        if serial <= 0 or serial in seen_serials or not all(math.isfinite(v) for v in coords):
            raise PoseExportError("Pose 含有重复或非有限原子坐标")
        seen_serials.add(serial)
        pose_coords[serial] = coords

    assigned_atoms: set[int] = set()
    mapped_serials: set[int] = set()
    for smiles_atom_idx, pdbqt_serial in smiles_idx_pairs:
        atom_idx = smiles_atom_idx - 1
        coords = pose_coords.get(pdbqt_serial)
        if (
            coords is None
            or atom_idx < 0
            or atom_idx >= mol.GetNumAtoms()
            or atom_idx in assigned_atoms
            or pdbqt_serial in mapped_serials
        ):
            raise PoseExportError("Pose 原子映射不完整或不唯一")
        conf.SetAtomPosition(atom_idx, Point3D(*coords))
        assigned_atoms.add(atom_idx)
        mapped_serials.add(pdbqt_serial)

    hydrogen_map: dict[int, list[int]] = {}
    for parent_smiles_idx, pdbqt_serial in h_parent_pairs:
        hydrogen_map.setdefault(parent_smiles_idx - 1, []).append(pdbqt_serial)
    for parent_idx, hydrogen_serials in hydrogen_map.items():
        if parent_idx < 0 or parent_idx >= mol.GetNumAtoms():
            raise PoseExportError("Pose 氢原子映射无效")
        hydrogen_neighbors = [
            nbr.GetIdx()
            for nbr in mol.GetAtomWithIdx(parent_idx).GetNeighbors()
            if nbr.GetAtomicNum() == 1
        ]
        if len(hydrogen_serials) > len(hydrogen_neighbors):
            raise PoseExportError("Pose 氢原子映射不完整")
        for h_idx, pdbqt_serial in zip(hydrogen_neighbors, hydrogen_serials):
            coords = pose_coords.get(pdbqt_serial)
            if coords is None:
                raise PoseExportError("Pose 氢原子映射不完整")
            if h_idx in assigned_atoms or pdbqt_serial in mapped_serials:
                raise PoseExportError("Pose 原子映射不唯一")
            conf.SetAtomPosition(h_idx, Point3D(*coords))
            assigned_atoms.add(h_idx)
            mapped_serials.add(pdbqt_serial)

    heavy_atoms = {atom.GetIdx() for atom in mol.GetAtoms() if atom.GetAtomicNum() > 1}
    if not heavy_atoms.issubset(assigned_atoms):
        raise PoseExportError("Pose 未完整映射全部重原子")
    export_mol = mol if keep_hydrogens else Chem.RemoveHs(mol)
    sdf_block = Chem.MolToMolBlock(export_mol)
    if not sdf_block:
        raise PoseExportError("无法导出标准 SDF", 500)
    return sdf_block
