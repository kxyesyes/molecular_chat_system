import pytest


def test_reverse_target_status_distinguishes_empty_results_from_completed_results():
    from src.web.routes.reverse_target_routes import _reverse_result_status

    assert _reverse_result_status([]) == {
        "status": "failed",
        "success": False,
        "error_code": "NO_MATCHING_TARGETS",
    }
    assert _reverse_result_status([{"target_name": "PDE5A"}]) == {
        "status": "completed",
        "success": True,
    }


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], "failed"),
        ([{"success": False, "status": "no_match"}], "failed"),
        ([{"success": True, "status": "completed", "targets": [{"target_name": "PDE5A"}]}], "completed"),
        (
            [
                {"success": True, "status": "completed", "targets": [{"target_name": "PDE5A"}]},
                {"success": False, "status": "failed", "error": "invalid"},
            ],
            "partial",
        ),
    ],
)
def test_reverse_target_batch_status_is_not_partial_as_success(rows, expected):
    from src.web.routes.reverse_target_routes import _reverse_batch_status

    summary = _reverse_batch_status(rows)
    assert summary["status"] == expected
    assert summary["success"] is (expected == "completed")


def test_predict_batch_marks_empty_target_matches_as_no_match(monkeypatch):
    from src.reverse_target.predictor import ReverseTargetPredictor

    predictor = object.__new__(ReverseTargetPredictor)

    def predict(*args, **kwargs):
        return []

    monkeypatch.setattr(predictor, "predict", predict)

    rows = predictor.predict_batch(["CCO"])

    assert rows == [
        {
            "row_index": 0,
            "query_smiles": "CCO",
            "success": False,
            "status": "no_match",
            "error_code": "NO_MATCHING_TARGETS",
            "targets": [],
        }
    ]


def test_reverse_target_aggregation_keeps_same_name_across_species_separate():
    from src.reverse_target.predictor import _aggregate_by_target, _target_aggregation_key

    human = {
        "target_name": "PDE5A",
        "target_chembl_id": "CHEMBL123",
        "organism": "Homo sapiens",
        "taxon_id": 9606,
        "final_3d_score": 0.8,
    }
    mouse = {
        **human,
        "organism": "Mus musculus",
        "taxon_id": 10090,
        "final_3d_score": 0.7,
    }

    assert _target_aggregation_key(human) != _target_aggregation_key(mouse)
    assert len(_aggregate_by_target([human, mouse], top_k=10)) == 2
