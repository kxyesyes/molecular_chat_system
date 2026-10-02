from __future__ import annotations

import math
import threading

from src.agent.contracts import ToolResult
from src.agent.validators.domain_validators import ActivityResultValidator
from src.activity.predictor import ActivityPredictor


def _provenance(**overrides):
    value = {
        "model_id": "model-1",
        "weights_sha256": "a" * 64,
        "model_path": "data/activity/models/model-1.pt",
        "demo_mode": False,
        "fallback_used": False,
    }
    value.update(overrides)
    return value


def _canonical_result(**changes):
    row = {
        "smiles": "CCO",
        "success": True,
        "task_type": "regression",
        "endpoint": "pIC50",
        "value": 6.1,
        "units": "pIC50",
        "model_provenance": _provenance(),
    }
    row.update(changes.pop("row", {}))
    quality = {"model_provenance": _provenance()}
    quality.update(changes.pop("quality", {}))
    payload = dict(
        tool_name="activity_predictor",
        success=True,
        message="",
        data=[row],
        quality=quality,
        evidence=[{"prediction": dict(row)}],
    )
    payload.update(changes)
    return ToolResult(**payload)


def test_canonical_activity_success_requires_complete_provenance_and_evidence():
    assert ActivityResultValidator().validate(_canonical_result()) is None

    for mutation in (
        {"row": {"task_type": None}},
        {"row": {"endpoint": None}},
        {"row": {"units": None}},
        {"row": {"value": math.nan}},
        {"row": {"model_provenance": {"demo_mode": False}}},
        {"row": {"model_provenance": None}, "quality": {"model_provenance": {"demo_mode": False}}},
        {"evidence": []},
    ):
        assert ActivityResultValidator().validate(_canonical_result(**mutation))


def test_legacy_activity_score_cannot_claim_success_without_canonical_contract():
    result = ToolResult.success_result(
        "activity_predictor",
        data=[{"smiles": "CCO", "success": True, "activity_score": 6.1}],
        quality={"model_provenance": _provenance()},
        evidence=[{"prediction": {"smiles": "CCO", "activity_score": 6.1}}],
    )

    assert ActivityResultValidator().validate(result)


def test_inflight_prediction_keeps_immutable_model_snapshot_during_invalidation():
    torch = __import__("torch")
    data_module = __import__("torch_geometric.data", fromlist=["Data"])
    entered = threading.Event()
    release = threading.Event()

    class BlockingModel:
        def __call__(self, atom_batch, _rg_batch):
            entered.set()
            assert release.wait(2)
            return torch.tensor([6.4] * atom_batch.num_graphs), None

    predictor = ActivityPredictor()
    predictor._loaded = True
    predictor.demo_mode = False
    predictor.device = torch.device("cpu")
    predictor.current_model_metadata = {
        "model_id": "before-switch",
        "weights_sha256": "b" * 64,
        "task_type": "regression",
        "endpoint": "pIC50",
        "units": "pIC50",
    }
    predictor.current_model_path = "before-switch.pt"
    predictor.model = BlockingModel()
    predictor.process_smiles = lambda _smi: (
        data_module.Data(x=torch.ones((1, 1))),
        data_module.Data(x=torch.ones((1, 1))),
    )
    holder = {}
    worker = threading.Thread(target=lambda: holder.setdefault("rows", predictor.predict("CCO")))
    worker.start()
    assert entered.wait(2)
    predictor.invalidate()
    release.set()
    worker.join(2)

    assert worker.is_alive() is False
    assert holder["rows"][0]["success"] is True
    assert holder["rows"][0]["model_provenance"]["model_id"] == "before-switch"


def test_training_labels_reject_implicit_continuous_to_classification_conversion():
    from src.activity import trainer

    assert trainer._prepare_training_labels([0, 1], task_type="classification") == [0.0, 1.0]
    assert trainer._prepare_training_labels(
        [4.9, 5.0, 6.2],
        task_type="classification",
        classification_threshold=5.0,
        classification_direction="greater_or_equal",
    ) == [0.0, 1.0, 1.0]

    for values in ([4.9, 5.0, 6.2], [0, 0], [0.0, math.nan]):
        try:
            trainer._prepare_training_labels(values, task_type="classification")
        except ValueError:
            pass
        else:
            raise AssertionError("invalid classification labels must be rejected")


def test_random_split_keeps_canonical_duplicate_molecules_in_one_partition():
    from src.activity import trainer

    result = trainer._split_indices(
        ["CCO", "OCC", "CCN", "NCC", "c1ccccc1", "C1=CC=CC=C1"],
        split_strategy="random",
        random_seed=9,
        validation_fraction=0.34,
    )
    train = set(result["train_indices"])
    validation = set(result["val_indices"])
    assert not ({0, 1} & train and {0, 1} & validation)
    assert not ({2, 3} & train and {2, 3} & validation)
    assert not ({4, 5} & train and {4, 5} & validation)


def test_rgmpnn_layer_configuration_is_valid_for_one_and_many_layers():
    from src.activity.rg_mpnn.Nets.ReduceGNN import RGNN

    for count in (1, 5):
        model = RGNN(
            in_channels=4,
            channels=8,
            out_channels=1,
            edge_dim=2,
            num_passing_atom=count,
                num_passing_pool=1,
                num_passing_rg=1,
                num_passing_mol=1,
                implementation_version=2,
            )
        assert len(model.atom_convs) == count
        assert len(model.atom_lins) == count
        assert len(model.atom_res_lins) == count
        assert model.implementation_version == 2


def test_legacy_gnn_single_atom_layer_does_not_index_missing_residual():
    torch = __import__("torch")
    data_module = __import__("torch_geometric.data", fromlist=["Data"])
    from src.activity.rg_mpnn.Nets.ReduceGNN import GNN

    model = GNN(in_channels=4, channels=8, out_channels=1, edge_dim=2,
                num_passing_atom=1, num_passing_mol=1)
    data = data_module.Data(
        x=torch.ones((2, 4)),
        edge_index=torch.tensor([[0, 1], [1, 0]], dtype=torch.long),
        edge_attr=torch.ones((2, 2)),
        batch=torch.zeros(2, dtype=torch.long),
    )
    output, fingerprint = model(data, None)

    assert output.shape == (1,)
    assert fingerprint.shape == (1, 8)


def test_valid_single_atom_smiles_is_reported_as_unsupported_not_invalid():
    predictor = ActivityPredictor()
    predictor._loaded = True
    predictor.demo_mode = False
    predictor.current_model_metadata = {
        "model_id": "model-1",
        "weights_sha256": "a" * 64,
        "task_type": "regression",
        "endpoint": "pIC50",
        "units": "pIC50",
    }
    predictor.model = object()

    result = predictor.predict("[Na+]")[0]

    assert result["success"] is False
    assert result["error"] == "unsupported_no_bond_structure"


def test_legacy_batch_preserves_partial_status_and_canonical_provenance():
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool

    class StubPredictor:
        demo_mode = False
        current_model_metadata = {
            "model_id": "legacy-model",
            "weights_sha256": "c" * 64,
            "task_type": "regression",
            "endpoint": "pIC50",
            "units": "pIC50",
        }

        def predict(self, _smiles):
            return [
                {
                    "smiles": "CCO", "success": True,
                    "task_type": "regression", "endpoint": "pIC50",
                    "value": 6.2, "units": "pIC50",
                },
                {"smiles": "CCN", "success": False, "error": "invalid_smiles"},
            ]

    tool = ActivityPredictorTool()
    tool._predictor = StubPredictor()
    raw = tool.execute({"smiles": ["CCO", "CCN"]})

    assert raw["success"] is False
    assert raw["status"] == "partial"
    assert raw["quality"]["prediction_status"] == "partial"
    assert len(raw["evidence"]) == 1
    assert raw["data"][0]["model_provenance"]["fallback_used"] is False


def test_legacy_batch_with_no_success_is_failed_without_scientific_values():
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool

    class StubPredictor:
        demo_mode = False
        current_model_metadata = {}

        def predict(self, _smiles):
            return [{"smiles": "CCO", "success": False, "error": "model unavailable"}]

    tool = ActivityPredictorTool()
    tool._predictor = StubPredictor()
    raw = tool.execute({"smiles": "CCO"})

    assert raw["success"] is False
    assert raw["status"] == "failed"
    assert raw["data"][0]["success"] is False
    assert "value" not in raw["data"][0]
    assert "probability" not in raw["data"][0]


def test_activity_batch_csv_uses_selected_smiles_column_and_skips_header():
    from src.web.routes.activity_prediction_routes import _parse_batch_smiles

    values = _parse_batch_smiles(
        "compound_id,structure,activity\na,CCO,1\nb,CCN,2\n",
        filename="screen.csv",
        smiles_column="structure",
    )

    assert values == ["CCO", "CCN"]


def test_activity_batch_csv_missing_selected_column_fails_closed():
    from src.web.routes.activity_prediction_routes import _parse_batch_smiles

    try:
        _parse_batch_smiles(
            "name,activity\na,1\n",
            filename="screen.csv",
            smiles_column="structure",
        )
    except ValueError as exc:
        assert "SMILES" in str(exc)
    else:
        raise AssertionError("CSV without the selected SMILES column must be rejected")
