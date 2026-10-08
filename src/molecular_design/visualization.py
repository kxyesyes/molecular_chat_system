"""RDKit-backed molecular visualization and comparison operations.

This module owns the scientific calculation.  Web adapters translate its
domain errors to HTTP responses, while Agent callers can reuse the same
results without importing FastAPI or route code.
"""

from __future__ import annotations

import io
from typing import Any


class MoleculeVisualizationError(ValueError):
    """A validated molecular visualization operation could not complete."""

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


def _mol_from_smiles(smiles: str, *, message: str):
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise MoleculeVisualizationError(message, code="invalid_smiles")
    return mol


def smiles_to_3d(smiles: str) -> dict[str, Any]:
    """Generate and optimize one 3D conformer using the existing RDKit path."""
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = _mol_from_smiles(smiles, message="无效的SMILES字符串")
    mol = Chem.AddHs(mol)
    embed_result = AllChem.EmbedMolecule(mol, randomSeed=42)
    if embed_result != 0:
        params = AllChem.ETKDGv3()
        params.randomSeed = 42
        embed_result = AllChem.EmbedMolecule(mol, params)
    if embed_result != 0:
        raise MoleculeVisualizationError("无法生成3D构象", code="embedding_failed")

    if AllChem.MMFFHasAllMoleculeParams(mol):
        force_field = "MMFF"
        optimization_status = AllChem.MMFFOptimizeMolecule(mol)
    elif AllChem.UFFHasAllMoleculeParams(mol):
        force_field = "UFF"
        optimization_status = AllChem.UFFOptimizeMolecule(mol)
    else:
        raise MoleculeVisualizationError(
            "缺少可用的力场参数，无法确认3D结构质量",
            code="force_field_unavailable",
        )
    if optimization_status != 0:
        raise MoleculeVisualizationError(
            f"{force_field} 3D结构优化未收敛，未返回合格构象",
            code="optimization_not_converged",
        )

    mol_no_h = Chem.RemoveHs(mol)
    pdb_block = Chem.MolToPDBBlock(mol_no_h)
    if not pdb_block:
        raise MoleculeVisualizationError("无法转换为PDB格式", code="pdb_conversion_failed")

    return {
        "success": True,
        "pdb_data": pdb_block,
        "message": "3D结构生成成功",
    }


def smiles_to_image(smiles: str, width: int, height: int) -> bytes:
    """Render a validated molecule as PNG bytes."""
    from rdkit import Chem
    from rdkit.Chem import Draw

    if not smiles:
        raise MoleculeVisualizationError("SMILES cannot be empty", code="empty_smiles")
    mol = _mol_from_smiles(smiles, message="Invalid SMILES")
    image = Draw.MolToImage(mol, size=(width, height))
    image_bytes = io.BytesIO()
    image.save(image_bytes, format="PNG")
    return image_bytes.getvalue()


def _bond_indices_in_match(mol, atom_indices):
    atom_set = set(map(int, atom_indices))
    return [
        int(bond.GetIdx())
        for bond in mol.GetBonds()
        if int(bond.GetBeginAtomIdx()) in atom_set
        and int(bond.GetEndAtomIdx()) in atom_set
    ]


def mcs(
    smiles1: str,
    smiles2: str,
    width: int,
    height: int,
    timeout: int,
) -> dict[str, Any]:
    """Calculate and render the maximum common substructure."""
    from rdkit import Chem
    from rdkit.Chem import rdFMCS
    from rdkit.Chem.Draw import rdMolDraw2D

    mol1 = _mol_from_smiles(smiles1, message="Invalid SMILES")
    mol2 = _mol_from_smiles(smiles2, message="Invalid SMILES")
    mcs_res = rdFMCS.FindMCS(
        [mol1, mol2],
        timeout=timeout,
        ringMatchesRingOnly=True,
        completeRingsOnly=True,
        matchValences=True,
    )
    mcs_smarts = (mcs_res.smartsString or "").strip()
    if not mcs_smarts:
        return {"success": False, "error": "MCS not found"}

    mcs_mol = Chem.MolFromSmarts(mcs_smarts)
    if mcs_mol is None:
        return {"success": False, "error": "MCS parse failed", "mcs_smarts": mcs_smarts}

    match1 = mol1.GetSubstructMatch(mcs_mol)
    match2 = mol2.GetSubstructMatch(mcs_mol)
    if not match1 or not match2:
        return {"success": False, "error": "MCS match failed", "mcs_smarts": mcs_smarts}

    highlight_color = (0.2, 0.7, 0.9)
    atom_colors1 = {int(atom): highlight_color for atom in match1}
    atom_colors2 = {int(atom): highlight_color for atom in match2}

    def mol_svg(mol, highlight_atoms, atom_colors):
        drawer = rdMolDraw2D.MolDraw2DSVG(int(width), int(height))
        options = drawer.drawOptions()
        options.addAtomIndices = False
        if hasattr(options, "fillHighlights"):
            options.fillHighlights = True
        if hasattr(options, "highlightBondWidthMultiplier"):
            options.highlightBondWidthMultiplier = 18
        bonds = _bond_indices_in_match(mol, highlight_atoms)
        rdMolDraw2D.PrepareAndDrawMolecule(
            drawer,
            mol,
            highlightAtoms=list(map(int, highlight_atoms)),
            highlightBonds=bonds,
            highlightAtomColors=atom_colors,
            highlightBondColors={int(bond): highlight_color for bond in bonds},
        )
        drawer.FinishDrawing()
        return drawer.GetDrawingText()

    bond_ids1 = _bond_indices_in_match(mol1, match1)
    bond_ids2 = _bond_indices_in_match(mol2, match2)
    return {
        "success": True,
        "mcs_smarts": mcs_smarts,
        "query_highlight_atoms": [int(atom) for atom in match1],
        "hit_highlight_atoms": [int(atom) for atom in match2],
        "query_highlight_bonds": bond_ids1,
        "hit_highlight_bonds": bond_ids2,
        "query_svg": mol_svg(mol1, match1, atom_colors1),
        "hit_svg": mol_svg(mol2, match2, atom_colors2),
    }


__all__ = ["MoleculeVisualizationError", "mcs", "smiles_to_3d", "smiles_to_image"]
