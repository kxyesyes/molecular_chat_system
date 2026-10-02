import pandas as pd

from src.reverse_target.evaluation import (
    evaluate_ranked_predictions,
    scaffold_split,
    time_split,
)


def test_ranked_metrics_are_computed_from_labels_not_similarity_thresholds():
    rows = [
        {"query_id": "q1", "target_id": "T2", "score": 0.95, "label": 0},
        {"query_id": "q1", "target_id": "T1", "score": 0.90, "label": 1},
        {"query_id": "q2", "target_id": "T3", "score": 0.80, "label": 1},
        {"query_id": "q2", "target_id": "T4", "score": 0.20, "label": 0},
    ]
    metrics = evaluate_ranked_predictions(rows, top_k=1)
    assert metrics["top_k_recall"] == 0.5
    assert metrics["mrr"] == 0.75
    assert 0 <= metrics["enrichment_factor"]
    assert 0 <= metrics["bedroc"] <= 1
    assert 0 <= metrics["pr_auc"] <= 1
    assert metrics["calibration"]["available"] is False
    assert metrics["calibration"]["reason"] == "score_is_similarity_not_probability"


def test_probability_calibration_requires_explicit_probability_values():
    rows = [
        {"query_id": "q1", "score": 0.9, "probability": 0.8, "label": 1},
        {"query_id": "q1", "score": 0.2, "probability": 0.1, "label": 0},
    ]

    calibration = evaluate_ranked_predictions(rows)["calibration"]

    assert calibration["available"] is True
    assert 0 <= calibration["ece"] <= 1
    assert 0 <= calibration["brier"] <= 1


def test_scaffold_split_is_deterministic_and_disjoint():
    frame = pd.DataFrame(
        {
            "canonical_smiles": ["c1ccccc1", "c1ccccc1O", "CCO", "CCCO", "CCN"],
            "target_id": ["A", "A", "B", "B", "C"],
        }
    )
    first = scaffold_split(frame, seed=42)
    second = scaffold_split(frame, seed=42)
    assert [part.index.tolist() for part in first] == [part.index.tolist() for part in second]
    assert set(first[0].index).isdisjoint(first[1].index)
    assert set(first[0].index).isdisjoint(first[2].index)
    assert set(first[1].index).isdisjoint(first[2].index)
    assert sum(len(part) for part in first) == len(frame)


def test_time_split_orders_observations_and_prevents_future_leakage():
    frame = pd.DataFrame(
        {
            "assay_date": ["2024-03-01", "2024-01-01", "2024-02-01", "2024-04-01"],
            "canonical_smiles": ["CCO", "CCN", "CCC", "CCCC"],
        },
        index=[10, 11, 12, 13],
    )

    train, validation, test = time_split(frame, date_column="assay_date")

    assert train["assay_date"].max() <= validation["assay_date"].min()
    assert validation["assay_date"].max() <= test["assay_date"].min()
    assert sum(len(part) for part in (train, validation, test)) == len(frame)
    assert set(train.index).isdisjoint(validation.index)
    assert set(train.index).isdisjoint(test.index)
    assert set(validation.index).isdisjoint(test.index)
