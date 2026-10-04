from unittest import mock

from src.agent.tools import admet_predictor as admet_module
from src.agent.tools.admet_predictor import ADMETPredictor


def test_missing_admet_ai_is_explicit_and_never_rdkit_fallback():
    with mock.patch.object(admet_module, "get_admet_ai_backend", return_value=None), \
            mock.patch.object(admet_module, "admet_ai_backend_error", return_value="package missing"):
        result = ADMETPredictor().execute("Predict ADMET for CCO")

    assert result["success"] is False
    assert result["status"] == "unavailable"
    assert result["data"] is None
    assert "ADMET-AI" in result["message"]
    assert "rdkit" in result["reasoning"].lower()
    assert "prediction_method" not in str(result)


def test_legacy_rdkit_helper_remains_explicitly_non_model():
    predictor = ADMETPredictor(backend=None)
    props = predictor._predict_admet_with_rdkit("CCO")

    assert props["prediction_method"] == "rdkit_rules"
    assert "physicochemical" in props
    assert "solubility" in props
    assert "pharmacokinetics" in props
    assert "druglikeness" in props
    assert "非训练模型预测" in predictor.format_admet_result("CCO", props)
