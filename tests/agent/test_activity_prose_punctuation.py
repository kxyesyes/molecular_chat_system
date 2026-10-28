"""Sentence periods are prose, not permission to repair molecular fields."""
import pytest

from src.agent.tools.activity_input import activity_target, parse_activity_input
from src.agent.tools.activity_predictor_tool import ActivityPredictorTool


@pytest.fixture
def tool():
    return ActivityPredictorTool()


@pytest.mark.parametrize("query, expected", [
    ("Compare properties and BuChE activity for CCO and CCN.", ["CCO", "CCN"]),
    ("Compare properties and BuChE activity for CCO and CCN", ["CCO", "CCN"]),
    ("Predict BuChE activity for CCO.", ["CCO"]),
    ("Please assess BuChE activity for CCO.  ", ["CCO"]),
    ("Evaluate BuChE activity of CCN.", ["CCN"]),
    ("Compare BuChE activity for CCO and [Na+].[Cl-].", ["CCO", "[Na+].[Cl-]"]),
    ("Compare BuChE activity for CCO and CCN.CCO.", ["CCO", "CCN.CCO"]),
    ("Compare BuChE activity for CCO and [Na+].[Cl-]", ["CCO", "[Na+].[Cl-]"]),
    ("Predict BuChE activity for CCO.; SMILES: CCN", ["CCO", "CCN"]),
    ("Compare properties and BuChE activity for CCO and CCN. Keep original predictions and consistency checks.", ["CCO", "CCN"]),
    ("Compare properties and BuChE activity for CCO and CCN.CCO. Show original results and exclusion reasons.", ["CCO", "CCN.CCO"]),
])
def test_prose_period_preserves_complete_molecules_and_original_query(tool, query, expected):
    assert parse_activity_input(query, tool) == (query, expected, "BuChE")


def test_structured_query_uses_same_prose_boundary(tool):
    query = "Compare properties and BuChE activity for CCO and CCN."
    assert parse_activity_input({"query": query, "smiles": ["CCO", "CCN"]}, tool) == (
        query, ["CCO", "CCN"], "BuChE",
    )
    assert tool.should_use(query)


def test_structured_comparison_retains_original_display_query(tool):
    query = "Compare properties and BuChE activity for CCO and CCN. Keep original predictions and consistency checks."
    assert parse_activity_input({"query": query, "smiles": ["CCO", "CCN"]}, tool) == (
        query, ["CCO", "CCN"], "BuChE",
    )


@pytest.mark.parametrize("value", [
    "CCN..", "CCN...", "CCN.$bad", "CCN.foo.", "CCN)INVALID.",
    "CCN.[Cl-", "[Na+]..[Cl-].", "[Na+].[Cl-]..",
])
@pytest.mark.parametrize("display", ["", " Keep raw predictions and consistency checks."])
def test_prose_does_not_repair_or_split_invalid_structure(tool, value, display):
    with pytest.raises(ValueError):
        parse_activity_input(f"Compare BuChE activity for CCO and {value}{display}", tool)


@pytest.mark.parametrize("suffix", [
    "EGFR.", "BuChE.EGFR.", "BuChE$EGFR.", "EGFR and CCN.",
])
def test_unknown_target_suffix_is_not_erased_by_sentence_handling(tool, suffix):
    with pytest.raises(ValueError, match="靶点"):
        parse_activity_input(f"Predict BuChE activity for CCO and {suffix}", tool)


@pytest.mark.parametrize("field", [
    "CCO.", "CCO..", "[Na+].[Cl-].", "CCN.foo.", '"CCO".', "CCO name.",
])
@pytest.mark.parametrize("separator", ["; ", " "])
def test_explicit_smiles_fields_are_never_sentence_normalized(tool, field, separator):
    with pytest.raises(ValueError, match="SMILES"):
        parse_activity_input(f"Predict BuChE activity{separator}SMILES: {field}", tool)


@pytest.mark.parametrize("value", ["CCO.", "[Na+].[Cl-].", "CCN.."])
def test_structured_smiles_are_never_sentence_normalized(tool, value):
    with pytest.raises(ValueError, match="SMILES"):
        parse_activity_input({"target": "BuChE", "smiles": value}, tool)


@pytest.mark.parametrize("query", ["CCO.", "CCO.\nCCN", "CCO activity."])
def test_structure_leading_fields_do_not_borrow_prose_context(tool, query):
    with pytest.raises(ValueError, match="SMILES"):
        parse_activity_input({"query": query, "target": "BuChE"}, tool)


def test_period_normalization_does_not_cross_explicit_field_boundary(tool):
    with pytest.raises(ValueError, match="SMILES"):
        parse_activity_input("Predict BuChE activity for CCO; SMILES: CCN.", tool)


@pytest.mark.parametrize("value", ["CCN", "[Na+].[Cl-]", "CCN.CCO"])
def test_direct_target_api_accepts_prose_period_with_callable_validator(tool, value):
    query = f"Compare properties and BuChE activity for CCO and {value}."
    assert activity_target(query, lambda value: tool.validate_smiles(value)) == "BuChE"


@pytest.mark.parametrize("value", ["EGFR.", "BuChE.EGFR.", "CCN..", "CCN.foo."])
def test_direct_target_api_preserves_invalid_positioned_values(tool, value):
    with pytest.raises(ValueError):
        activity_target(
            f"Compare BuChE activity for CCO and {value}",
            lambda value: tool.validate_smiles(value),
        )


@pytest.mark.parametrize("display", ["", " Keep raw predictions and consistency checks."])
def test_terminal_period_keeps_activity_evidence_bound_to_display(display):
    from src.agent.evidence_followup import accepted_activity_evidence

    # Synthetic observation envelope only: no model inference or invented scores.
    result = {
        "trace_id": "synthetic-period-binding",
        "tool_result_sequence": [{
            "tool_name": "activity_predictor", "status": "succeeded", "success": True,
            "data": [{"smiles": value, "requested_target": "BuChE", "family_id": "buche-family"}
                     for value in ("CCO", "CCN")],
            "quality": {"validated": True, "evidence_id": "synthetic-period-evidence"},
            "provenance": {
                "tool_name": "activity_predictor", "input_digest": "synthetic-period-digest",
                "demo_mode": False, "fallback_used": False,
            },
        }],
    }
    base = "Compare properties and BuChE activity for CCO and CCN."
    assert accepted_activity_evidence(result, base[:-1], "synthetic-session") is not None
    query = base + display
    evidence = accepted_activity_evidence(result, query, "synthetic-session")
    assert evidence is not None
    assert evidence["query"] == query
    assert evidence["smiles"] == ["CCO", "CCN"]
    assert evidence["observations"][0]["data"] == result["tool_result_sequence"][0]["data"]
