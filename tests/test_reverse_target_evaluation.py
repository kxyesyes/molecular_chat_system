import pandas as pd

from src.reverse_target.evaluation import (
    evaluate_ranked_predictions,
    scaffold_split,
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
    assert 0 <= metrics["calibration"]["ece"] <= 1


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
