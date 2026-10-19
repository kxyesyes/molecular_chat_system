from src.agent.contracts import ToolResult, ToolProvenance
from src.web.decision_chat import scientific_display_answer


def test_property_result_display_is_human_readable_and_keeps_provenance():
    result = ToolResult.success_result(
        "property_calculator",
        data=[{"smiles": "CCO", "properties": {
            "molecular_formula": "C2H6O",
            "molecular_weight": 46.07,
            "logp": -0.001,
            "tpsa": 20.23,
            "hbd": 1,
            "hba": 1,
            "qed": 0.407,
            "rotatable_bonds": 0,
        }}],
        quality={"evidence_id": "evidence-1"},
        provenance=ToolProvenance(
            tool_name="property_calculator", tool_version="rdkit-1",
            input_digest="in", output_digest="out",
        ),
    )

    answer = scientific_display_answer([result])

    assert "分子量" in answer
    assert "46.07" in answer
    assert "CCO" in answer
    assert "evidence-1" in answer
    assert '"molecular_weight"' not in answer
