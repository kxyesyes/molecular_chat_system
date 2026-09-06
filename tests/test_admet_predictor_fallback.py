from unittest import mock

from src.agent.tools import admet_predictor as admet_module
from src.agent.tools.admet_predictor import ADMETPredictor


def test_admet_predictor_uses_rdkit_fallback_when_adme_py_is_missing():
    predictor = ADMETPredictor()

    with mock.patch.object(admet_module, "ADME_PY_AVAILABLE", False):
        result = predictor.execute("Predict ADMET for CCO")

    assert result["success"] is True
    assert result["data"][0]["smiles"] == "CCO"
    admet = result["data"][0]["admet"]
    assert admet["prediction_method"] == "rdkit_rules"
    assert "physicochemical" in admet
    assert "solubility" in admet
    assert "pharmacokinetics" in admet
    assert "druglikeness" in admet
