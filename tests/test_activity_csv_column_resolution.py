from __future__ import annotations

import pandas as pd
import pytest

from src.activity import trainer


def test_resolve_training_columns_uses_normalized_names_for_lookup_and_values():
    frame = pd.DataFrame({"  SMILES  ": ["CCO"], " P I C 5 0 ": [5.2], "custom": ["x"]})

    resolved = trainer._resolve_training_columns(
        frame,
        smiles_column=" smiles ",
        target_column="p i c 5 0",
    )

    assert resolved == ("  SMILES  ", " P I C 5 0 ")
    assert frame[resolved[0]].tolist() == ["CCO"]
    assert frame[resolved[1]].tolist() == [5.2]


@pytest.mark.parametrize(
    ("columns", "requested"),
    [
        (["molecule", "label"], ("MOLECULE", " LABEL ")),
        (["custom_smiles", "activity"], (" custom_smiles ", "ACTIVITY")),
    ],
)
def test_resolve_training_columns_supports_case_and_custom_names(columns, requested):
    frame = pd.DataFrame({columns[0]: ["CCN"], columns[1]: [6.1]})

    assert trainer._resolve_training_columns(
        frame,
        smiles_column=requested[0],
        target_column=requested[1],
    ) == tuple(columns)


def test_resolve_training_columns_rejects_duplicate_normalized_headers():
    frame = pd.DataFrame([["CCO", "CCN", 5.0]], columns=["SMILES", " smiles ", "label"])

    with pytest.raises(ValueError, match="ambiguous.*SMILES"):
        trainer._resolve_training_columns(
            frame,
            smiles_column="smiles",
            target_column="label",
        )
