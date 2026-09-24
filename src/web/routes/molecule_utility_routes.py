"""Molecule utility route registration."""
from typing import Dict, Any
from fastapi import Body, Query, HTTPException, Response


def setup_molecule_utility_routes(app, *, _support):
    """Register the original endpoints with dynamically resolved compatibility support."""
    @app.post("/api/docking/smiles_to_3d")
    async def smiles_to_3d(payload: Dict[str, Any] = Body(...)):
        """将SMILES转换为3D结构用于预览"""
        try:
            smiles = payload.get("smiles", "").strip()
            if not smiles:
                raise HTTPException(status_code=400, detail="SMILES字符串不能为空")
            
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

            # 去掉氢原子后转 PDB 用于可视化：
            # 氢原子颜色为白色，白色背景下不可见，且球模式下会产生视觉噪点
            mol_noH = Chem.RemoveHs(mol)
            pdb_block = Chem.MolToPDBBlock(mol_noH)
            
            if not pdb_block:
                raise HTTPException(status_code=500, detail="无法转换为PDB格式")
            
            return {
                "success": True,
                "pdb_data": pdb_block,
                "message": "3D结构生成成功"
            }
            
        except HTTPException:
            raise
        except Exception as e:
            _support.logger.error(f"SMILES转3D失败: {e}")
            raise HTTPException(status_code=500, detail=f"转换失败: {str(e)}")

    @app.get("/api/utils/smiles_to_image")
    async def smiles_to_image(
        smiles: str,
        width: int = Query(300, ge=64, le=2048),
        height: int = Query(200, ge=64, le=2048),
    ):
        """生成分子2D图片"""
        try:
            from rdkit import Chem
            from rdkit.Chem import Draw
            import io
            
            if not smiles:
                 raise HTTPException(status_code=400, detail="SMILES cannot be empty")

            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                raise HTTPException(status_code=400, detail="Invalid SMILES")
                
            # Generate Image
            img = Draw.MolToImage(mol, size=(width, height))
            
            # Convert to bytes
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='PNG')
            img_byte_arr.seek(0)
            
            return Response(content=img_byte_arr.getvalue(), media_type="image/png")
            
        except Exception as e:
            _support.logger.error(f"生成分子图片失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/utils/mcs")
    async def get_mcs(
        smiles1: str,
        smiles2: str,
        width: int = Query(360, ge=64, le=2048),
        height: int = Query(260, ge=64, le=2048),
        timeout: int = Query(3, ge=1, le=30),
    ):
        """计算两分子的最大公共子结构(MCS)，返回SMARTS与高亮SVG"""
        try:
            from rdkit import Chem
            from rdkit.Chem import rdFMCS
            from rdkit.Chem.Draw import rdMolDraw2D

            if not smiles1 or not smiles2:
                raise HTTPException(status_code=400, detail="SMILES cannot be empty")

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
            atom_colors1 = {int(a): highlight_color for a in match1}
            atom_colors2 = {int(a): highlight_color for a in match2}

            def _bond_indices_in_match(mol, atom_indices):
                atom_set = set(map(int, atom_indices))
                bond_ids = []
                for b in mol.GetBonds():
                    a1 = int(b.GetBeginAtomIdx())
                    a2 = int(b.GetEndAtomIdx())
                    if a1 in atom_set and a2 in atom_set:
                        bond_ids.append(int(b.GetIdx()))
                return bond_ids

            bond_ids1 = _bond_indices_in_match(mol1, match1)
            bond_ids2 = _bond_indices_in_match(mol2, match2)
            bond_colors1 = {int(b): highlight_color for b in bond_ids1}
            bond_colors2 = {int(b): highlight_color for b in bond_ids2}

            def _mol_svg(mol, highlight_atoms, atom_colors):
                drawer = rdMolDraw2D.MolDraw2DSVG(int(width), int(height))
                opts = drawer.drawOptions()
                opts.addAtomIndices = False
                # 让共同片段更“块状”而不是只显示圆点
                if hasattr(opts, "fillHighlights"):
                    opts.fillHighlights = True
                if hasattr(opts, "highlightBondWidthMultiplier"):
                    opts.highlightBondWidthMultiplier = 18
                rdMolDraw2D.PrepareAndDrawMolecule(
                    drawer,
                    mol,
                    highlightAtoms=list(map(int, highlight_atoms)),
                    highlightBonds=_bond_indices_in_match(mol, highlight_atoms),
                    highlightAtomColors=atom_colors,
                    highlightBondColors={int(b): highlight_color for b in _bond_indices_in_match(mol, highlight_atoms)},
                )
                drawer.FinishDrawing()
                return drawer.GetDrawingText()

            return {
                "success": True,
                "mcs_smarts": mcs_smarts,
                "query_highlight_atoms": [int(a) for a in match1],
                "hit_highlight_atoms": [int(a) for a in match2],
                "query_highlight_bonds": [int(b) for b in bond_ids1],
                "hit_highlight_bonds": [int(b) for b in bond_ids2],
                "query_svg": _mol_svg(mol1, match1, atom_colors1),
                "hit_svg": _mol_svg(mol2, match2, atom_colors2),
            }
        except HTTPException:
            raise
        except Exception as e:
            _support.logger.error(f"MCS计算失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))
