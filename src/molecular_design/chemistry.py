"""RDKit chemistry operations for molecular design."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .optimizer import evaluate_goals, parse_optimization_goals


def _mol_from_smiles(smiles: str, label: str = "SMILES"):
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"无效{label}: {smiles}")
    return mol


def canonicalize_smiles(smiles: str) -> str:
    from rdkit import Chem

    mol = _mol_from_smiles(smiles)
    return Chem.MolToSmiles(mol, canonical=True)


def describe_site_environment(mol, site_idx: int) -> str:
    atom = mol.GetAtomWithIdx(site_idx)
    neighbors = atom.GetNeighbors()
    if not neighbors:
        return "孤立位点"

    neighbor = neighbors[0]
    parts: List[str] = []
    if neighbor.GetIsAromatic():
        parts.append("芳香环C")
    else:
        hybridization = str(neighbor.GetHybridization())
        if "SP3" in hybridization:
            parts.append("sp3碳")
        elif "SP2" in hybridization:
            parts.append("sp2碳")

    if mol.GetRingInfo().NumAtomRings(neighbor.GetIdx()) > 0:
        parts.append("环上")
    return " ".join(parts) if parts else "脂肪碳"


def detect_sites(smiles: str) -> Dict[str, Any]:
    mol = _mol_from_smiles(smiles)
    sites = []
    for atom in mol.GetAtoms():
        if atom.GetAtomicNum() == 0:
            idx = atom.GetIdx()
            neighbors = [
                {
                    "idx": neighbor.GetIdx(),
                    "symbol": neighbor.GetSymbol(),
                    "is_aromatic": neighbor.GetIsAromatic(),
                    "hybridization": str(neighbor.GetHybridization()),
                }
                for neighbor in atom.GetNeighbors()
            ]
            sites.append(
                {
                    "atom_idx": idx,
                    "map_num": atom.GetAtomMapNum(),
                    "neighbors": neighbors,
                    "env_desc": describe_site_environment(mol, idx),
                }
            )
    return {"success": True, "sites": sites, "total": len(sites)}


def auto_mark_site(smiles: str) -> Optional[str]:
    from rdkit import Chem

    mol = _mol_from_smiles(smiles)
    for atom in mol.GetAtoms():
        if atom.GetAtomicNum() == 6 and atom.GetIsAromatic() and atom.GetTotalNumHs() > 0:
            rw = Chem.RWMol(mol)
            target_atom = rw.GetAtomWithIdx(atom.GetIdx())
            target_atom.SetNoImplicit(True)
            target_atom.SetNumExplicitHs(0)
            dummy_idx = rw.AddAtom(Chem.Atom(0))
            rw.AddBond(atom.GetIdx(), dummy_idx, Chem.BondType.SINGLE)
            try:
                Chem.SanitizeMol(rw)
                return Chem.MolToSmiles(rw.GetMol(), canonical=True)
            except Exception:
                continue
    return None


def _find_dummy_and_neighbor(mol):
    for atom in mol.GetAtoms():
        if atom.GetAtomicNum() == 0:
            neighbors = atom.GetNeighbors()
            if neighbors:
                return atom.GetIdx(), neighbors[0].GetIdx()
    return None, None


def _do_substitution(parent_mol, fragment_mol):
    from rdkit import Chem

    parent_dummy, parent_connect = _find_dummy_and_neighbor(parent_mol)
    fragment_dummy, fragment_connect = _find_dummy_and_neighbor(fragment_mol)
    if parent_dummy is None or fragment_dummy is None:
        return None

    combo = Chem.CombineMols(parent_mol, fragment_mol)
    parent_atom_count = parent_mol.GetNumAtoms()
    fragment_connect_in_combo = fragment_connect + parent_atom_count
    fragment_dummy_in_combo = fragment_dummy + parent_atom_count

    rw = Chem.RWMol(combo)
    rw.AddBond(parent_connect, fragment_connect_in_combo, Chem.BondType.SINGLE)
    for dummy_idx in sorted([parent_dummy, fragment_dummy_in_combo], reverse=True):
        rw.RemoveAtom(dummy_idx)

    try:
        Chem.SanitizeMol(rw)
    except Exception as exc:
        raise ValueError(
            "化学取代失败：生成产物未通过 RDKit 结构校验，"
            "可能存在价态、芳香性或连接点不兼容。请尝试更换片段或手动标记 [*] 位点。"
        ) from exc
    return rw.GetMol()


def substitute_fragment(parent_smiles: str, fragment_smiles: str) -> Dict[str, Any]:
    from rdkit import Chem

    parent_mol = _mol_from_smiles(parent_smiles, "母体分子 SMILES")
    has_dummy = any(atom.GetAtomicNum() == 0 for atom in parent_mol.GetAtoms())
    if not has_dummy:
        marked = auto_mark_site(parent_smiles)
        if not marked:
            raise ValueError("未找到取代位点，请在 SMILES 中插入 [*] 或在编辑器中标记原子")
        parent_smiles = marked
        parent_mol = _mol_from_smiles(parent_smiles, "母体分子 SMILES")

    fragment_mol = _mol_from_smiles(fragment_smiles, "片段 SMILES")
    product = _do_substitution(parent_mol, fragment_mol)
    if product is None:
        raise ValueError("化学取代失败，可能存在价键冲突或缺少 [*] 连接点")

    return {
        "success": True,
        "new_smiles": Chem.MolToSmiles(product, canonical=True),
        "parent_smiles": parent_smiles,
        "fragment_smiles": fragment_smiles,
    }


def calculate_scaffold_similarity(reference_smiles: str, candidate_smiles: str) -> Optional[float]:
    if not reference_smiles or not candidate_smiles:
        return None
    try:
        from rdkit import Chem, DataStructs

        ref = Chem.MolFromSmiles(reference_smiles)
        cand = Chem.MolFromSmiles(candidate_smiles)
        if ref is None or cand is None:
            return None
        try:
            from rdkit.Chem import rdFingerprintGenerator

            generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
            ref_fp = generator.GetFingerprint(ref)
            cand_fp = generator.GetFingerprint(cand)
        except Exception:
            from rdkit.Chem import AllChem

            ref_fp = AllChem.GetMorganFingerprintAsBitVect(ref, 2, nBits=2048)
            cand_fp = AllChem.GetMorganFingerprintAsBitVect(cand, 2, nBits=2048)
        return round(float(DataStructs.TanimotoSimilarity(ref_fp, cand_fp)), 4)
    except Exception:
        return None


def calculate_properties(
    smiles: str,
    command: str = "",
    reference_smiles: str = "",
) -> Dict[str, Any]:
    from rdkit.Chem import Descriptors, rdMolDescriptors
    from rdkit.Chem.Lipinski import NumHAcceptors, NumHDonors
    from rdkit.Chem.QED import qed

    mol = _mol_from_smiles(smiles)
    property_status = {}
    props = {
        "logp": round(float(Descriptors.MolLogP(mol)), 3),
        "mw": round(float(Descriptors.MolWt(mol)), 3),
        "tpsa": round(float(rdMolDescriptors.CalcTPSA(mol)), 3),
        "hbd": int(NumHDonors(mol)),
        "hba": int(NumHAcceptors(mol)),
        "rotbonds": int(rdMolDescriptors.CalcNumRotatableBonds(mol)),
        "qed": round(float(qed(mol)), 4),
        "fsp3": round(float(rdMolDescriptors.CalcFractionCSP3(mol)), 3),
    }

    try:
        from rdkit.Chem import RDConfig
        import os
        import sys

        sa_path = os.path.join(RDConfig.RDContribDir, "SA_Score")
        if sa_path not in sys.path:
            sys.path.append(sa_path)
        import sascorer

        props["sa_score"] = round(float(sascorer.calculateScore(mol)), 3)
        property_status["sa_score"] = "ok"
    except Exception:
        props["sa_score"] = None
        property_status["sa_score"] = "unavailable"

    scaffold_similarity = calculate_scaffold_similarity(reference_smiles, smiles)
    if scaffold_similarity is not None:
        props["scaffold_similarity"] = scaffold_similarity

    goals = parse_optimization_goals(command)

    return {
        "success": True,
        "properties": props,
        "goals": evaluate_goals(props, goals),
        "property_status": property_status,
    }
