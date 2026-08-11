from __future__ import annotations

from src.agent.tools.base_tool import BaseMolecularTool


class Tool(BaseMolecularTool):
    def __init__(self):
        super().__init__("test", "test")


def test_extract_smiles_rejects_prompt_labels_without_rdkit_validation(monkeypatch):
    tool = Tool()
    validated = []
    original = tool.validate_smiles

    def recording_validate(value):
        validated.append(value)
        return original(value)

    monkeypatch.setattr(tool, "validate_smiles", recording_validate)

    result = tool.extract_smiles(
        "SMILES: RG-MPNN partial/failed Hit-to-lead Objectives: request: "
        "properties: QED/LogP/pIC50/kcal/mol"
    )

    assert result == []
    assert validated == []


def test_extract_smiles_preserves_common_structures_and_simple_cco():
    tool = Tool()

    result = tool.extract_smiles("SMILES: CC(=O)Oc1ccccc1C(=O)O")

    assert "CC(=O)Oc1ccccc1C(=O)O" in result
    assert tool.extract_smiles("calculate CCO") == ["CCO"]
