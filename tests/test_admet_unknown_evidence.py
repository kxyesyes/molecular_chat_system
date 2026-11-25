"""ADMET evidence semantics; actual in-memory RDKit, controlled optional backend."""
from copy import deepcopy

import pytest
from rdkit import Chem, rdBase
from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors

from src.admet import legacy as module
from src.admet.predictor import ADMETPredictor


@pytest.mark.parametrize("smiles", ["CCO", "CC(=O)Oc1ccccc1C(=O)O", "CCCCCCCCCCCCCCCC", "O"])
def test_rdkit_values_formulas_counts_and_legacy_units_preserved(monkeypatch, smiles):
    monkeypatch.setattr(module, "ADME_PY_AVAILABLE", False)
    tool = ADMETPredictor()
    props = tool.predict_admet_with_adme_py(smiles)
    mol = Chem.MolFromSmiles(smiles)
    mw, logp, tpsa = Descriptors.MolWt(mol), Crippen.MolLogP(mol), Descriptors.TPSA(mol)
    rot, hbd, hba = Lipinski.NumRotatableBonds(mol), Lipinski.NumHDonors(mol), Lipinski.NumHAcceptors(mol)
    heavy = mol.GetNumHeavyAtoms()
    aromatic = sum(atom.GetIsAromatic() for atom in mol.GetAtoms())
    log_s = 0.16 - 1.5 * logp - 0.01 * (mw - 40) + 0.066 * rot + 0.066 * (aromatic / heavy if heavy else 0)
    assert props["prediction_method"] == "rdkit_rules"
    assert props["backend_version"] == rdBase.rdkitVersion
    assert props["physicochemical"] == {
        "formula": rdMolDescriptors.CalcMolFormula(mol), "molecular_weight": mw,
        "num_heavy_atoms": heavy, "num_aromatic_atoms": aromatic,
        "sp3_carbon_ratio": rdMolDescriptors.CalcFractionCSP3(mol),
        "num_rotatable_bonds": rot, "num_h_donors": hbd, "num_h_acceptors": hba,
        "molar_refractivity": Crippen.MolMR(mol), "tpsa": tpsa,
    }
    assert props["lipophilicity"] == {"wlogp": logp}
    assert props["solubility"]["log_s_esol"] == pytest.approx(log_s, rel=1e-14)
    assert props["solubility"]["solubility_esol"] == pytest.approx(max(0, 10 ** log_s * mw), rel=1e-14)
    expected_class = next((label for threshold, label in (
        (-1, "Very Soluble"), (-2, "Soluble"), (-3, "Moderately Soluble"), (-4, "Poorly Soluble")
    ) if log_s >= threshold), "Insoluble")
    assert props["solubility"]["class_esol"] == expected_class
    assert props["pharmacokinetics"] == {
        "gastrointestinal_absorption": "High" if mw <= 500 and tpsa <= 140 else "Low",
        "blood_brain_barrier_permeant": 0 <= logp <= 5 and tpsa < 90,
        "skin_permeability_logkp": -2.72 + 0.71 * logp - 0.0061 * mw,
    }
    assert props["druglikeness"] == {
        "lipinski": "Pass" if sum((mw > 500, logp > 5, hbd > 5, hba > 10)) <= 1 else "Fail",
        "veber": "Pass" if rot <= 10 and tpsa <= 140 else "Fail", "ghose": {},
    }
    assert props["medicinal"]["synthetic_accessibility"] == min(10, max(1, 1 + heavy / 25 + rot / 5 + mol.GetRingInfo().NumRings() / 4))
    assert props["medicinal"]["leadlikeness"] == {}
    # Preserve the existing display label, not a validation of the underlying units.
    assert "mg/mL" in tool.format_admet_result(smiles, props)


SECTIONS = ("physicochemical", "solubility", "lipophilicity", "pharmacokinetics", "druglikeness", "medicinal")
MISSING = object()
UNKNOWN = "未知（未计算或无有效结果）"
NO_ASSESSMENT = "未形成可解释的ADME评估；缺失项目未知。"


def sparse():
    return {"prediction_method": "adme_py", "backend_version": "fixture-only", **{name: {} for name in SECTIONS}}


def line(report, name):
    return next(value for value in report.splitlines() if f"**{name}：**" in value)


def test_rdkit_uncomputed_alerts_and_sa_identity():
    props = module._predict_admet_with_rdkit("CCO")
    assert all(props["medicinal"][key] is None for key in ("pains", "brenk", "zinc"))
    assert props["medicinal"]["synthetic_accessibility_method"] == "rdkit_complexity_heuristic"
    assert props["medicinal"]["synthetic_accessibility"] == pytest.approx(1.12)


@pytest.mark.parametrize("key", ["pains", "brenk", "zinc"])
@pytest.mark.parametrize("value", [True, False, None, MISSING, 0, 1, "false", "true", [], {}])
def test_alerts_distinguish_false_from_unknown_without_mutation(key, value):
    props = sparse()
    if value is not MISSING:
        props["medicinal"][key] = value
    before = deepcopy(props)
    report = ADMETPredictor().format_admet_result("CCO", props)
    label = {"pains": "PAINS", "brenk": "Brenk", "zinc": "ZINC"}[key] + "警报"
    expected = "有（后端报告）" if value is True else "无（后端报告；不等于安全）" if value is False else UNKNOWN
    assert line(report, label).endswith(expected)
    assert props == before


@pytest.mark.parametrize("value", [True, False, None, MISSING, 0, 1, "false", [], {}])
def test_bbb_never_implies_cns_safety_or_activity(value):
    props = sparse()
    if value is not MISSING:
        props["pharmacokinetics"]["blood_brain_barrier_permeant"] = value
    report = ADMETPredictor().format_admet_result("CCO", props)
    expected = "该方法估计可透过" if value is True else "该方法估计不易透过" if value is False else UNKNOWN
    assert expected in line(report, "血脑屏障透过性")
    for forbidden in ("CNS副作用风险较低", "不太可能产生CNS副作用", "可能具有CNS活性"):
        assert forbidden not in report
    assert "不能据此判断CNS活性或副作用风险" in report


@pytest.mark.parametrize("section,rule,label", [
    ("druglikeness", "ghose", "Ghose规则"), ("medicinal", "leadlikeness", "先导化合物相似性"),
])
@pytest.mark.parametrize("value", [{}, None, "", "Unknown", [], {"MW": None},
                                  {"MW": "within range"}, {"MW": "unrecognized"}])
def test_incomplete_rules_never_default_to_pass_or_fail(section, rule, label, value):
    props = sparse()
    props[section][rule] = value
    text = line(ADMETPredictor().format_admet_result("CCO", props), label)
    assert "未知" in text
    assert "通过" not in text and "符合" not in text


@pytest.mark.parametrize("label,translated", [
    ("Very Soluble", "高溶解性"), ("Highly Soluble", "高溶解性"),
    ("Soluble", "中等溶解性"), ("Moderately Soluble", "中等溶解性"),
    ("Poorly Soluble", "低溶解性"), ("Very Poorly Soluble", "极低溶解性"),
    ("Insoluble", "难溶"), ("poorly soluble", "低溶解性"),
    (None, "未知"), ("mystery Soluble", "未知"), ({}, "未知"),
])
def test_whole_solubility_labels_not_substrings(label, translated):
    props = sparse()
    props["solubility"]["class_esol"] = label
    report = ADMETPredictor().format_admet_result("CCO", props)
    assert translated in line(report, "溶解性等级")
    if translated != "未知":
        assert translated in report.split("### 综合评估")[1]
    assert "具有良好的水溶性" not in report
    assert "优秀的水溶性" not in report


@pytest.mark.parametrize("section_value", [MISSING, None, {}, [], "N/A", 0])
def test_empty_and_malformed_sections_do_not_claim_assessment(section_value):
    props = {name: deepcopy(section_value) for name in SECTIONS} if section_value is not MISSING else {}
    before = deepcopy(props)
    report = ADMETPredictor().format_admet_result("CCO", props)
    assert NO_ASSESSMENT in report
    assert "未知" in line(report, "脂溶性等级")
    assert props == before


@pytest.mark.parametrize("section,key,label", [
    ("physicochemical", "molecular_weight", "分子量"),
    ("physicochemical", "num_heavy_atoms", "重原子数"),
    ("physicochemical", "sp3_carbon_ratio", "SP3碳比例"),
    ("lipophilicity", "wlogp", "WLogP值"),
    ("solubility", "solubility_esol", "溶解度"),
    ("pharmacokinetics", "skin_permeability_logkp", "皮肤透过性 LogKp"),
])
@pytest.mark.parametrize("value", [None, True, False, "1.2", float("nan"), float("inf"), float("-inf")])
def test_invalid_numeric_display_is_unknown(section, key, label, value):
    props = sparse()
    props[section][key] = value
    report = ADMETPredictor().format_admet_result("CCO", props)
    assert "未知" in line(report, label)
    assert props[section][key] is value


@pytest.mark.parametrize("section,key,label,value", [
    ("physicochemical", "num_heavy_atoms", "重原子数", -1),
    ("physicochemical", "num_h_donors", "氢键供体数", 1.5),
    ("physicochemical", "sp3_carbon_ratio", "SP3碳比例", 1.1),
    ("solubility", "solubility_esol", "溶解度", -1),
])
def test_invalid_display_ranges_do_not_become_valid(section, key, label, value):
    props = sparse()
    props[section][key] = value
    assert "未知" in line(ADMETPredictor().format_admet_result("CCO", props), label)
    assert props[section][key] == value


def test_valid_zero_negative_logs_and_method_labels():
    props = sparse()
    props["lipophilicity"]["wlogp"] = -2.0
    props["solubility"] = {"log_s_esol": -4.0, "solubility_esol": 0.0}
    props["pharmacokinetics"]["skin_permeability_logkp"] = -3.0
    report = ADMETPredictor().format_admet_result("CCO", props)
    for value in ("-2.000", "-4.000", "0.000", "-3.000", "adme_py", "fixture-only"):
        assert value in report
    assert "强亲水性" in report
    rdkit = module._predict_admet_with_rdkit("CCO")
    report = ADMETPredictor().format_admet_result("CCO", rdkit)
    assert "RDKit-rule" in report
    assert "非训练模型预测、非实验结果" in report
    assert "局部结构复杂度启发式" in report
    assert "非标准SA评分" in report
    assert "越低越容易合成" not in report


@pytest.mark.parametrize("value", [0, 11, None, True, "2.1", float("nan"), float("inf")])
def test_unusable_sa_does_not_infer_synthetic_ease(value):
    props = sparse()
    props["medicinal"]["synthetic_accessibility"] = value
    report = ADMETPredictor().format_admet_result("CCO", props)
    assert "合成难度较低" not in report and "合成难度较高" not in report
    assert NO_ASSESSMENT in report


def legacy_result(monkeypatch, payload):
    class FixtureADME:
        def __init__(self, smiles):
            assert smiles in ("CCO", "CCN")
        def calculate(self):
            return deepcopy(payload)
    monkeypatch.setattr(module, "ADME_PY_AVAILABLE", True)
    monkeypatch.setattr(module, "ADME", FixtureADME)
    monkeypatch.setattr(module, "ADME_PY_VERSION", "fixture-only")
    return ADMETPredictor().predict_admet_with_adme_py("CCO")


@pytest.mark.parametrize("section,leaf,value", [
    ("physiochemical", "molecular_weight", 46.07), ("lipophilicity", "wlogp", 0),
    ("pharmacokinetics", "blood_brain_barrier_permeant", False), ("medicinal", "pains", False),
])
def test_partial_backend_retains_actual_leaf_without_imputing_missing(monkeypatch, section, leaf, value):
    props = legacy_result(monkeypatch, {section: {leaf: value}})
    assert props["prediction_method"] == "adme_py" and props["backend_version"] == "fixture-only"
    assert props["physicochemical" if section == "physiochemical" else section] == {leaf: value}


@pytest.mark.parametrize("source", [
    "https://example.org/adme/reference",
    "http://docs.example.org/adme/reference-v1.2/",
    "https://example.org",
])
def test_legacy_backend_keeps_public_source_and_diagnostics(monkeypatch, source):
    payload = {"source": source, "provenance": {"source": source},
               "reason": "controlled unavailable", "warnings": ["controlled warning"]}
    before = deepcopy(payload)
    metadata = legacy_result(monkeypatch, payload)
    for key in payload:
        assert metadata[key] == payload[key]
    assert payload == before


@pytest.mark.parametrize("source", [
    "https://example.org/C:/private/backend.py",
    "https://example.org/adme/c:/Users/Example/backend.py",
    "https://example.org/adme/file:///C:/Users/Example/backend.py",
    "https://example.org/adme/file:///private/backend.py",
    "https://example.org/adme//machine/share/backend.py",
    r"https://example.org/adme/C:\private\backend.py",
    r"https://example.org/adme/\\machine\share\backend.py",
    "https://example.org/adme/file://machine/share/backend.py",
], ids=["drive", "nested-drive", "file-drive", "file-posix", "forward-unc",
        "backslash-drive", "backslash-unc", "file-unc"])
def test_embedded_machine_paths_are_not_restored(monkeypatch, source):
    payload = {"source": source, "provenance": {"source": source}}
    metadata = legacy_result(monkeypatch, payload)
    assert metadata["source"] != source
    assert metadata["provenance"]["source"] != source
    assert "backend.py" not in str(metadata)


@pytest.mark.parametrize("source", [
    "https://user:synthetic-password@example.org/adme/reference",
    "https://example.org/adme/reference?token=synthetic-token",
    "https://example.org/adme/reference?download=synthetic-value",
    "https://example.org/adme/reference#token=synthetic-token",
    "https://example.org/token/synthetic-token",
    "".join(("https://example.org/adme/", "sk-", "abcdefghijklmnopqrstuvwxyz", "123456")),
    "https://example.org/%74oken/synthetic-token",
    "https://user%40example.org/adme/reference",
    "https://example.org/adme/reference%3Ftoken%3Dsynthetic-token",
    "file:///private/backend.py", "/private/backend.py",
    "C:/private/backend.py", r"C:\private\backend.py", r"\\private\backend.py",
])
def test_unsafe_diagnostic_sources_are_not_restored(monkeypatch, source):
    metadata = legacy_result(monkeypatch, {"source": source, "provenance": {"source": source}})
    assert metadata["source"] != source
    assert metadata["provenance"]["source"] != source


def test_source_preservation_does_not_bypass_diagnostic_bounds_or_secret_keys():
    source = "https://example.org/adme/reference"
    metadata = module._diagnostic_metadata({
        "provenance": {"source": source, "password": {"source": source},
                       "nested": {"nested": {"source": source},
                                  "items": [{"source": source}] * 65}},
        "warnings": [source], "error": {"message": source},
    })
    assert metadata["provenance"]["source"] == source
    assert metadata["provenance"]["password"] == "[redacted]"
    assert metadata["provenance"]["nested"]["nested"]["source"] == source
    assert metadata["provenance"]["nested"]["items"] == ["[redacted]"] * 64
    assert source not in str(metadata["warnings"]) + str(metadata["error"])
    assert module._diagnostic_metadata({"source": source + "a" * 2048})["source"] != source + "a" * 2048


@pytest.mark.parametrize("container", ["dict", "list"])
def test_source_restoration_keeps_cycles_bounded_without_mutating_input(container):
    source = "https://example.org/adme/reference"
    provenance = {"source": source}
    provenance["cycle"] = provenance if container == "dict" else [provenance]
    metadata = module._diagnostic_metadata({"provenance": provenance})
    assert metadata["provenance"]["source"] == source
    if container == "dict":
        assert metadata["provenance"]["cycle"]["cycle"]["cycle"] == "[redacted]"
        assert provenance["cycle"] is provenance
    else:
        assert metadata["provenance"]["cycle"][0]["cycle"] == "[redacted]"
        assert provenance["cycle"][0] is provenance
    assert provenance["source"] == source


def test_empty_backend_without_diagnostics_does_not_invent_source_or_error(monkeypatch):
    props = legacy_result(monkeypatch, {})
    assert not ADMETPredictor._has_observations(props)
    assert "error" not in props and "source" not in props


def test_failure_metadata_keeps_safe_reason_without_private_payload(monkeypatch):
    payload = {"reason": "controlled no coverage", "failure_reason": "controlled unavailable",
               "provenance": {"source": "controlled fixture"},
               "error": {"code": "NO_COVERAGE", "message": "controlled unavailable",
                         "details": {"password": "synthetic-placeholder", "path": "/private/backend.py"}}}
    metadata = legacy_result(monkeypatch, payload)
    assert metadata["reason"] == payload["reason"]
    assert metadata["failure_reason"] == payload["failure_reason"]
    assert metadata["provenance"] == payload["provenance"]
    assert metadata["error"]["code"] == "NO_COVERAGE"
    assert metadata["error"]["message"] == "controlled unavailable"
    assert "synthetic-placeholder" not in str(metadata)
    assert "/private/backend.py" not in str(metadata)


@pytest.mark.parametrize("leaf,value,expected", [
    ("formula", "C2H6O", True), ("formula", "Unknown", False), ("formula", "N/A", False),
    ("molecular_weight", 46.069, True), ("molecular_weight", True, False),
    ("num_heavy_atoms", 0, True), ("num_heavy_atoms", -1, False),
    ("num_heavy_atoms", 1.5, False), ("sp3_carbon_ratio", 0, True),
    ("sp3_carbon_ratio", 1.1, False), ("molecular_weight", 10 ** 500, False),
])
def test_observation_presence_does_not_impute_or_recalculate(leaf, value, expected):
    props = sparse()
    props["physicochemical"][leaf] = value
    before = deepcopy(props)
    assert ADMETPredictor()._has_observations(props) is expected
    assert props == before


@pytest.mark.parametrize("rule", ["ghose", "leadlikeness"])
def test_actual_partial_rule_details_are_not_a_completed_pass(monkeypatch, rule):
    section = "druglikeness" if rule == "ghose" else "medicinal"
    props = legacy_result(monkeypatch, {section: {rule: {"MW": "within range"}}})
    assert props[section][rule] == {"MW": "within range"}
    report = ADMETPredictor().format_admet_result("CCO", props)
    assert "完整结论未知" in report
    assert "通过" not in report


def test_sa_backend_method_missing_or_supplied_is_not_invented():
    props = sparse()
    props["medicinal"]["synthetic_accessibility"] = 2.125
    tool = ADMETPredictor()
    report = tool.format_admet_result("CCO", props)
    assert "具体方法未提供" in report and "局部结构复杂度启发式" not in report
    props["medicinal"]["synthetic_accessibility_method"] = "controlled method"
    assert "controlled method" in tool.format_admet_result("CCO", props)
    assert props["medicinal"]["synthetic_accessibility"] == 2.125


def test_explicit_legacy_backend_exception_does_not_return_estimates(monkeypatch):
    def unavailable(*args):
        raise RuntimeError("controlled backend unavailable")

    monkeypatch.setattr(module, "ADME_PY_AVAILABLE", True)
    monkeypatch.setattr(module, "ADME", unavailable)
    assert ADMETPredictor().predict_admet_with_adme_py("CCO") is None
