"""Input fidelity tests; RDKit rules are not trained ADMET predictions."""
import pytest

from src.agent.tools import admet_predictor as module


class RecordingBackend:
    version = "1.4.0"
    weights_id = "sha256:" + "a" * 64

    def __init__(self, calls):
        self.calls = calls

    def predict_batch(self, smiles, molecule_ids):
        self.calls.extend(smiles)
        return [
            {
                "molecule_id": molecule_id,
                "smiles": value,
                "canonical_smiles": value,
                "status": "succeeded",
                "admet": {
                    "prediction_method": "admet_ai",
                    "backend_version": "1.4.0",
                    "model_name": "ADMET-AI",
                    "model_version": "1.4.0",
                    "weights_id": self.weights_id,
                    "demo_mode": False,
                    "fallback_used": False,
                    "physicochemical_source": "rdkit_calculation",
                    "endpoints": {"HIA_Hou": {
                        "value": 0.9, "unit": "-", "task_type": "classification",
                        "source": "admet_ai_model",
                    }},
                    "units": {"HIA_Hou": "-"},
                    "risk_endpoint_ids": [],
                    "risk_count": 0,
                    "total_endpoints": 1,
                    "risk_threshold": 0.5,
                    "risk_summary_method": "fixture",
                },
                "warnings": [],
            }
            for value, molecule_id in zip(smiles, molecule_ids)
        ]


def recording_predictor(calls):
    return module.ADMETPredictor(backend=RecordingBackend(calls))


def test_admet_receives_every_complete_input_not_just_known_ethanol(monkeypatch):
    calls = []
    predictor = recording_predictor(calls)
    result = predictor.execute("CCO\nOCC\nCCN")
    assert calls == ["CCO", "OCC", "CCN"]
    assert result["success"]
    assert [row["smiles"] for row in result["data"]] == calls
    assert all(row["admet"]["prediction_method"] == "admet_ai" for row in result["data"])


@pytest.mark.parametrize("query", [
    "SMILES: CCO invalid_suffix", "SMILES: CCO\nSMILES: CC(C)((",
    "CCO invalid_suffix", "CCO\nCC(C)((", "SMILES: cco", "SMILES: CCO |name|",
    "BBB invalid_suffix\nCCO", "BBB permeability invalid_suffix\nCCO",
    "BBB invalid_suffix\nPredict permeability for CCO",
    "SMILES: BBB permeability\nCCO",
    "Predict BBB permeability for CCO and CNS)INVALID",
    "Predict ADMET for CCO and BBBpermeability)INVALID",
    "Predict ADMET for CCO and CNSpenetration.CCN",
    "Predict ADMET for CCO and CNSpenetration[",
])
def test_invalid_input_never_dispatches_valid_fragments(monkeypatch, query):
    predictor = module.ADMETPredictor()
    calls = []
    monkeypatch.setattr(predictor, "predict_admet_with_adme_py", lambda value: calls.append(value))
    result = predictor.execute(query)
    assert calls == []
    assert result["success"] is False and result["data"] is None
    assert not result["formatted"]
    assert "SMILES" in result["message"]


@pytest.mark.parametrize("query, expected", [
    ("请预测 ADMET。SMILES: CCN", True),
    ("Predict ADMET for CCO", True),
    ("请预测 ADMET。SMILES: CCO invalid_suffix", False),
    ("请预测 ADMET。SMILES: CCO\nSMILES: CC(C)((", False),
    ("你好", False),
])
def test_admet_trigger_uses_same_whole_input_contract(query, expected):
    assert module.ADMETPredictor().should_use(query) is expected


def test_missing_rdkit_validation_cannot_fall_back_to_lexical_acceptance(monkeypatch):
    monkeypatch.setattr(module, "parse_molecular_smiles", lambda *args, **kwargs: (_ for _ in ()).throw(
        module.MolecularInputUnavailable("RDKit 不可用，无法验证 SMILES。")
    ))
    calls = []
    predictor = recording_predictor(calls)
    result = predictor.execute("SMILES: CCO")
    assert not result["success"] and result["data"] is None and calls == []
    assert "RDKit" in result["message"]


def test_parser_failure_is_unavailable_not_a_structure_diagnosis(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("private parser diagnostic")

    monkeypatch.setattr(module, "parse_molecular_smiles", unavailable)
    predictor = module.ADMETPredictor()
    calls = []
    monkeypatch.setattr(predictor, "predict_admet_with_adme_py", lambda value: calls.append(value))
    result = predictor.execute("Predict ADMET for CCO")
    assert not result["success"] and result["data"] is None and calls == []
    assert "暂不可用" in result["message"]
    assert "private parser diagnostic" not in str(result)
    assert not predictor.should_use("Predict ADMET for CCO")


@pytest.mark.parametrize("query, expected", [
    ("Predict ADMET and BBB permeability for CCO", ["CCO"]),
    ("Predict BBB permeability. SMILES: CCO", ["CCO"]),
    ("请预测 CCO 的 ADMET 和 CNS 通透性", ["CCO"]),
    ("BBB permeability for CCO", ["CCO"]),
    ("Predict ADMET for BBB and CCO", ["BBB", "CCO"]),
    ("SMILES: BBB\nSMILES: CNS", ["BBB", "CNS"]),
    ("BBB\nCNS\nCCO", ["BBB", "CNS", "CCO"]),
])
def test_admet_prose_terms_do_not_add_molecules_but_explicit_fields_remain(monkeypatch, query, expected):
    calls = []
    predictor = recording_predictor(calls)
    result = predictor.execute(query)
    assert calls == expected
    assert result["success"]
    assert [row["smiles"] for row in result["data"]] == expected


@pytest.mark.parametrize("suffix", [".!", "!?", ".!?"])
@pytest.mark.parametrize("template", ["BBB permeability{}\nCCO", "Predict ADMET for CCO and BBBpermeability{}"])
def test_domain_phrase_only_tolerates_one_sentence_punctuation(monkeypatch, suffix, template):
    predictor = module.ADMETPredictor()
    calls = []
    monkeypatch.setattr(predictor, "predict_admet_with_adme_py", lambda value: calls.append(value))
    result = predictor.execute(template.format(suffix))
    assert calls == [] and not result["success"] and result["data"] is None


@pytest.mark.parametrize("phrase", ["BBB permeability", "CNS penetration"])
@pytest.mark.parametrize("suffix", [".CCN", "[", ".!?"])
def test_spaced_prose_phrase_cannot_hide_a_bad_suffix(monkeypatch, phrase, suffix):
    predictor = module.ADMETPredictor()
    calls = []
    monkeypatch.setattr(predictor, "predict_admet_with_adme_py", lambda value: calls.append(value))
    query = f"Predict ADMET for CCO and {phrase}{suffix}"
    result = predictor.execute(query)
    assert calls == [] and not result["success"] and result["data"] is None
    assert not predictor.should_use(query)


def test_underlying_rdkit_value_error_is_not_a_public_input_error(monkeypatch):
    from rdkit import Chem

    def failed(*args, **kwargs):
        raise ValueError("private parser diagnostic")

    monkeypatch.setattr(Chem, "MolFromSmiles", failed)
    predictor = module.ADMETPredictor()
    result = predictor.execute("SMILES: CCO")
    assert not result["success"] and result["data"] is None
    assert "暂不可用" in result["message"]
    assert "private parser diagnostic" not in str(result)


@pytest.mark.parametrize("phrase", ["BBB通透性", "BBB 渗透性", "CNS通透性", "CNS 渗透性"])
@pytest.mark.parametrize("suffix", ["[", ".!?"])
def test_chinese_prose_phrase_cannot_drop_adjacent_suffix(monkeypatch, phrase, suffix):
    predictor = module.ADMETPredictor()
    calls = []
    monkeypatch.setattr(predictor, "predict_admet_with_adme_py", lambda value: calls.append(value))
    query = f"请预测 CCO 的 ADMET 和 {phrase}{suffix}"
    result = predictor.execute(query)
    assert calls == [] and not result["success"] and result["data"] is None
    assert not predictor.should_use(query)
