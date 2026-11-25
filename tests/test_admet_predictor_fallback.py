import subprocess
import sys
from pathlib import Path
from unittest import mock

from src.admet import legacy, predictor as admet_module
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


def test_explicit_legacy_rules_remain_labelled_as_non_model(monkeypatch):
    monkeypatch.setattr(legacy, "ADME_PY_AVAILABLE", False)
    predictor = ADMETPredictor(backend=None)
    props = predictor.predict_admet_with_adme_py("CCO")

    assert props["prediction_method"] == "rdkit_rules"
    assert "physicochemical" in props
    assert "solubility" in props
    assert "pharmacokinetics" in props
    assert "druglikeness" in props
    assert "非训练模型预测" in predictor.format_admet_result("CCO", props)


def test_importing_model_predictor_does_not_probe_legacy_backend():
    script = """
import sys

class LegacyImportProbe:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "adme_py":
            raise AssertionError("loading the model predictor probed adme_py")
        return None

sys.meta_path.insert(0, LegacyImportProbe())
from src.admet.predictor import ADMETPredictor
"""
    subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], check=True
    )
