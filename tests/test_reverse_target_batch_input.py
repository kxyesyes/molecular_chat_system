import pytest

from src.reverse_target.batch_input import parse_batch_rows
from src.reverse_target.predictor import ReverseTargetPredictor


def test_csv_requires_an_explicit_smiles_column_and_preserves_order_duplicates():
    rows = parse_batch_rows("items.CSV", "name,smiles\na,CCO\nb,CCO\n")
    assert [(row["row_index"], row["smiles"]) for row in rows] == [(0, "CCO"), (1, "CCO")]


def test_csv_missing_smiles_column_is_an_error():
    with pytest.raises(ValueError, match="SMILES"):
        parse_batch_rows("items.csv", "name,value\na,1\n")


def test_text_rows_are_not_silently_split_or_truncated():
    rows = parse_batch_rows("items.txt", "CCO\n\nCCN\n")
    assert [row["smiles"] for row in rows] == ["CCO", "", "CCN"]


def test_unsupported_extension_is_an_error():
    with pytest.raises(ValueError, match="CSV|文本"):
        parse_batch_rows("items.xlsx", "CCO\n")


def test_predict_batch_returns_one_status_per_input_row_and_keeps_duplicates(monkeypatch):
    predictor = object.__new__(ReverseTargetPredictor)

    def predict(smiles, threshold, top_k, combine_by_target, organism_filter=""):
        if smiles == "CCN":
            raise ValueError("invalid test molecule")
        return [{"target_name": "T", "final_similarity": 1.0}]

    monkeypatch.setattr(predictor, "predict", predict)
    results = predictor.predict_batch(["CCO", "", "CCN", "CCO"])
    assert [item["row_index"] for item in results] == [0, 1, 2, 3]
    assert [item["success"] for item in results] == [True, False, False, True]
    assert results[1]["status"] == "invalid_input"
    assert results[2]["status"] == "failed"
