"""Scientific visualization is reusable without an HTTP adapter."""
import ast
from pathlib import Path

import pytest


def test_web_visualization_helpers_only_adapt_the_scientific_core():
    from src.web.routes import molecule_utility_routes

    source = Path(molecule_utility_routes.__file__).read_text(encoding="utf-8")
    assert "from rdkit" not in source
    assert "src.molecular_design" in source


def test_core_has_no_transport_or_agent_imports():
    from src.molecular_design import visualization

    tree = ast.parse(Path(visualization.__file__).read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    assert not any(name.startswith(("fastapi", "starlette", "src.web", "src.agent")) for name in imports)


@pytest.mark.parametrize("operation,args", [
    ("smiles_to_3d", ("CCO",)),
    ("smiles_to_image", ("CCO", 300, 200)),
    ("mcs", ("CCO", "CCC", 360, 260, 3)),
])
def test_http_helper_calls_core_once_and_preserves_result_identity(monkeypatch, operation, args):
    from src.molecular_design import visualization
    from src.web.routes import molecule_utility_routes

    calls = []
    result = object()

    def core(*received):
        calls.append(received)
        return result

    monkeypatch.setattr(visualization, operation, core)
    assert getattr(molecule_utility_routes, f"_{operation}_sync")(*args) is result
    assert calls == [args]


def test_real_core_outputs_match_existing_scientific_contract():
    from rdkit import Chem
    from src.molecular_design import visualization

    result = visualization.smiles_to_3d("CCO")
    assert result["success"] is True
    assert result["message"] == "3D结构生成成功"
    molecule = Chem.MolFromPDBBlock(result["pdb_data"])
    assert Chem.MolToSmiles(molecule) == "CCO"
    assert molecule.GetNumConformers() == 1
    assert visualization.smiles_to_3d("CCO") == result
    assert visualization.smiles_to_image("CCO", 300, 200).startswith(b"\x89PNG")
    result = visualization.mcs("CCO", "CCC", 360, 260, 3)
    assert result["success"] is True
    assert result["mcs_smarts"] == "[#6&!R]-&!@[#6&!R]"
    for prefix in ("query", "hit"):
        assert result[f"{prefix}_highlight_atoms"] == [0, 1]
        assert result[f"{prefix}_highlight_bonds"] == [0]
        assert "#33B2E5" in result[f"{prefix}_svg"]


@pytest.mark.parametrize("operation,args,detail", [
    ("smiles_to_3d", ("CC(C)((",), "无效的SMILES字符串"),
    ("smiles_to_image", ("CC(C)((", 300, 200), "Invalid SMILES"),
    ("smiles_to_image", ("", 300, 200), "SMILES cannot be empty"),
    ("mcs", ("CC(C)((", "CCC", 360, 260, 3), "Invalid SMILES"),
])
def test_domain_errors_are_not_http_errors_and_adapter_preserves_status(operation, args, detail):
    from fastapi import HTTPException
    from src.molecular_design import visualization
    from src.web.routes import molecule_utility_routes

    with pytest.raises(visualization.MoleculeVisualizationError) as error:
        getattr(visualization, operation)(*args)
    assert not isinstance(error.value, HTTPException)
    assert str(error.value) == detail
    with pytest.raises(HTTPException) as mapped:
        getattr(molecule_utility_routes, f"_{operation}_sync")(*args)
    assert mapped.value.status_code == 400
    assert mapped.value.detail == detail


def test_pdb_conversion_failure_remains_server_error(monkeypatch):
    from rdkit import Chem
    from fastapi import HTTPException
    from src.molecular_design import visualization
    from src.web.routes import molecule_utility_routes

    monkeypatch.setattr(Chem, "MolToPDBBlock", lambda mol: "")
    with pytest.raises(visualization.MoleculeVisualizationError) as error:
        visualization.smiles_to_3d("CCO")
    assert error.value.code == "pdb_conversion_failed"
    with pytest.raises(HTTPException) as mapped:
        molecule_utility_routes._smiles_to_3d_sync("CCO")
    assert mapped.value.status_code == 500
    assert mapped.value.detail == "无法转换为PDB格式"
