"""Molecule utility route registration."""
import io
from typing import Dict, Any

from fastapi import Body, Query, HTTPException, Response


def _smiles_to_3d_sync(smiles: str) -> dict[str, Any]:
    """Run the CPU-bound RDKit 3D preparation outside the event loop."""
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise HTTPException(status_code=400, detail="无效的SMILES字符串")

    mol = Chem.AddHs(mol)
    embed_result = AllChem.EmbedMolecule(mol, randomSeed=42)
    if embed_result != 0:
        params = AllChem.ETKDGv3()
        params.randomSeed = 42
        embed_result = AllChem.EmbedMolecule(mol, params)
    if embed_result != 0:
        raise HTTPException(status_code=400, detail="无法生成3D构象")

    AllChem.MMFFOptimizeMolecule(mol)
    mol_no_h = Chem.RemoveHs(mol)
    pdb_block = Chem.MolToPDBBlock(mol_no_h)
    if not pdb_block:
        raise HTTPException(status_code=500, detail="无法转换为PDB格式")

    return {
        "success": True,
        "pdb_data": pdb_block,
        "message": "3D结构生成成功",
    }


def _smiles_to_image_sync(smiles: str, width: int, height: int) -> bytes:
    """Render a molecule image with RDKit in a worker thread."""
    from rdkit import Chem
    from rdkit.Chem import Draw

    if not smiles:
        raise HTTPException(status_code=400, detail="SMILES cannot be empty")
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise HTTPException(status_code=400, detail="Invalid SMILES")

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


def _mcs_sync(
    smiles1: str,
    smiles2: str,
    width: int,
    height: int,
    timeout: int,
) -> dict[str, Any]:
    """Calculate and render an MCS with RDKit in a worker thread."""
    from rdkit import Chem
    from rdkit.Chem import rdFMCS
    from rdkit.Chem.Draw import rdMolDraw2D

    mol1 = Chem.MolFromSmiles(smiles1)
    mol2 = Chem.MolFromSmiles(smiles2)
    if mol1 is None or mol2 is None:
        raise HTTPException(status_code=400, detail="Invalid SMILES")

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
    bond_ids1 = _bond_indices_in_match(mol1, match1)
    bond_ids2 = _bond_indices_in_match(mol2, match2)

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


def setup_molecule_utility_routes(app, *, _support):
    """Register the original endpoints with dynamically resolved compatibility support."""
    @app.post("/api/docking/smiles_to_3d")
    async def smiles_to_3d(payload: Dict[str, Any] = Body(...)):
        """将SMILES转换为3D结构用于预览"""
        raw_smiles = payload.get("smiles", "")
        if not isinstance(raw_smiles, str):
            raise HTTPException(status_code=400, detail="SMILES字符串必须是字符串")
        smiles = raw_smiles.strip()
        if not smiles:
            raise HTTPException(status_code=400, detail="SMILES字符串不能为空")
        try:
            return await _support._invoke_in_threadpool(_smiles_to_3d_sync, smiles)
        except HTTPException:
            raise
        except Exception as error:
            _support.logger.error(f"SMILES转3D失败: {error}")
            raise HTTPException(status_code=500, detail=f"转换失败: {str(error)}")

    @app.get("/api/utils/smiles_to_image")
    async def smiles_to_image(
        smiles: str,
        width: int = Query(300, ge=64, le=2048),
        height: int = Query(200, ge=64, le=2048),
    ):
        """生成分子2D图片"""
        try:
            content = await _support._invoke_in_threadpool(
                _smiles_to_image_sync,
                smiles,
                width,
                height,
            )
            return Response(content=content, media_type="image/png")
        except HTTPException as error:
            # Preserve the legacy route contract: RDKit/input failures from
            # this image endpoint were exposed as HTTP 500 responses.
            legacy_detail = f"{error.status_code}: {error.detail}"
            _support.logger.error(f"生成分子图片失败: {legacy_detail}")
            raise HTTPException(status_code=500, detail=legacy_detail)
        except Exception as error:
            _support.logger.error(f"生成分子图片失败: {error}")
            raise HTTPException(status_code=500, detail=str(error))

    @app.get("/api/utils/mcs")
    async def get_mcs(
        smiles1: str,
        smiles2: str,
        width: int = Query(360, ge=64, le=2048),
        height: int = Query(260, ge=64, le=2048),
        timeout: int = Query(3, ge=1, le=30),
    ):
        """计算两分子的最大公共子结构(MCS)，返回SMARTS与高亮SVG"""
        if not smiles1 or not smiles2:
            raise HTTPException(status_code=400, detail="SMILES cannot be empty")
        try:
            return await _support._invoke_in_threadpool(
                _mcs_sync,
                smiles1,
                smiles2,
                width,
                height,
                timeout,
            )
        except HTTPException:
            raise
        except Exception as error:
            _support.logger.error(f"MCS计算失败: {error}")
            raise HTTPException(status_code=500, detail=str(error))
