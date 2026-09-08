"""Real RDKit/report tests; selected-SMILES fixtures isolate the legacy parser.

Main's query extractor predates the staged molecular-input fixes. These tests
stub selection only, never RDKit calculations, and are not parser acceptance.
"""
from copy import deepcopy

import pytest
from rdkit import Chem
from rdkit.Chem import Crippen, Descriptors, Lipinski, QED

from src.agent.tools.property_calculator import PropertyCalculator


SMILES = ("CCO", "CCN", "CC(=O)Oc1ccccc1C(=O)O")
RULE_LIMITS = {
    "molecular_weight": 500, "logp": 5, "hba": 10, "hbd": 5,
    "tpsa": 140, "rotatable_bonds": 10,
}
UNSUPPORTED = (
    "口服药物潜力", "口服药物性质", "综合成药潜力", "预测具有较好的药物性质",
    "口服吸收可能", "良好的膜透过性", "合理的生物利用度", "影响膜透过性",
    "细胞膜透过可能", "膜透过性可能受限", "选择性可能不足", "水溶性差",
    "构象稳定性可能较差", "通常是可接受的", "需显著结构改造", "需结构优化",
)


@pytest.fixture
def calculator():
    return PropertyCalculator()


def boundary_props(**changes):
    # Deliberately synthetic: these values only exercise prose thresholds.
    return {"molecular_formula": "synthetic fixture", "qed": 0.5,
            **RULE_LIMITS, **changes}


def prose_surfaces(calculator, props):
    return (
        calculator.format_properties("synthetic fixture", props),
        calculator._generate_brief_reasoning(props),
        calculator._generate_interpretation("synthetic fixture", props),
        calculator._generate_drug_chemistry_suggestions(props),
    )


def assert_bounded(text):
    assert "描述符" in text
    assert "启发式筛选" in text
    assert "不构成实验" in text
    assert not any(claim in text for claim in UNSUPPORTED)


@pytest.mark.parametrize("smiles", SMILES)
def test_real_descriptor_values_and_legacy_schema_are_unchanged(calculator, smiles, monkeypatch):
    mol = Chem.MolFromSmiles(smiles)
    expected = {
        "molecular_formula": Chem.rdMolDescriptors.CalcMolFormula(mol),
        "molecular_weight": round(Descriptors.MolWt(mol), 2),
        "logp": round(Crippen.MolLogP(mol), 3),
        "hba": Lipinski.NumHAcceptors(mol),
        "hbd": Lipinski.NumHDonors(mol),
        "tpsa": round(Descriptors.TPSA(mol), 2),
        "rotatable_bonds": Lipinski.NumRotatableBonds(mol),
        "qed": round(QED.qed(mol), 3),
    }
    assert calculator.calculate_properties(smiles) == expected
    monkeypatch.setattr(calculator, 'extract_smiles', lambda query: [smiles])
    result = calculator.execute(f"SMILES: {smiles}")
    assert result["success"], result["message"]
    assert result["data"] == [{"smiles": smiles, "properties": expected}]
    assert {"success", "data", "formatted", "reasoning", "message"} <= result.keys()
    assert_bounded(result["formatted"])
    assert_bounded(result["reasoning"])


def test_batch_reasoning_and_each_report_are_bounded(calculator, monkeypatch):
    monkeypatch.setattr(calculator, 'extract_smiles', lambda query: list(SMILES))
    result = calculator.execute("\n".join(f"SMILES: {s}" for s in SMILES))
    assert result["success"]
    assert [row["smiles"] for row in result["data"]] == list(SMILES)
    assert_bounded(result["reasoning"])
    for report in result["formatted"].split("## 🧬")[1:]:
        assert_bounded(report)
    assert result["formatted"].count("RDKit") >= len(SMILES)


def test_aspirin_report_with_main_query_parser_and_real_rdkit(calculator):
    smiles = 'CC(=O)Oc1ccccc1C(=O)O'
    result = calculator.execute(f'SMILES: {smiles}')
    assert result['success'], result['message']
    assert result['data'] == [{'smiles': smiles, 'properties': calculator.calculate_properties(smiles)}]
    assert_bounded(result['formatted'])
    assert_bounded(result['reasoning'])


@pytest.mark.parametrize("smiles", SMILES)
def test_single_report_does_not_repeat_summary_or_disclaimer(calculator, smiles, monkeypatch):
    monkeypatch.setattr(calculator, 'extract_smiles', lambda query: [smiles])
    result = calculator.execute(f"SMILES: {smiles}")
    assert result["success"]
    props = result["data"][0]["properties"]
    summary = calculator._assess_lipinski_rules(props)["overall_assessment"]
    assert result["formatted"].count(summary) == 1
    assert result["formatted"].count("不构成实验") == 1
    # The standalone legacy helper still supplies a complete bounded interpretation.
    assert summary in calculator._generate_interpretation(smiles, props)


@pytest.mark.parametrize("changes, lipinski_count, veber_count", [
    ({}, 0, 0),
    *[({key: limit + 1}, int(key not in ("tpsa", "rotatable_bonds")),
       int(key in ("tpsa", "rotatable_bonds"))) for key, limit in RULE_LIMITS.items()],
    ({key: limit + 1 for key, limit in RULE_LIMITS.items()}, 4, 2),
])
def test_four_lipinski_rules_are_separate_from_two_veber_checks(
    calculator, changes, lipinski_count, veber_count,
):
    props = boundary_props(**changes)
    assessment = calculator._assess_lipinski_rules(props)
    assert {"detailed_assessment", "overall_assessment", "violations_count"} <= assessment.keys()
    assert assessment["violations_count"] == lipinski_count
    assert f"Lipinski Ro5：已评估 4/4 项，违反 {lipinski_count} 项" in assessment["overall_assessment"]
    assert f"Veber：已评估 2/2 项，违反 {veber_count} 项" in assessment["overall_assessment"]
    assert "可旋转键" in assessment["detailed_assessment"]
    for text in prose_surfaces(calculator, props):
        assert assessment["overall_assessment"] in text


@pytest.mark.parametrize("key", RULE_LIMITS)
@pytest.mark.parametrize("missing", ["absent", "none"])
def test_missing_rule_input_is_explicit_and_never_complete_pass(calculator, key, missing):
    props = boundary_props()
    if missing == "absent":
        props.pop(key)
    else:
        props[key] = None
    before = deepcopy(props)
    assessment = calculator._assess_lipinski_rules(props)
    assert "缺失" in assessment["detailed_assessment"]
    assert "不完整" in assessment["overall_assessment"]
    assert "不能判定全部符合" in assessment["overall_assessment"]
    coverage = "Veber：已评估 1/2 项" if key in ("tpsa", "rotatable_bonds") else "Lipinski Ro5：已评估 3/4 项"
    assert coverage in assessment["overall_assessment"]
    for text in prose_surfaces(calculator, props):
        assert_bounded(text)
        assert assessment["overall_assessment"] in text
    assert props == before


@pytest.mark.parametrize("changes", [
    {"molecular_weight": value} for value in (100, 160, 501)
] + [{"logp": value} for value in (-1.0, 0.0, 3.0, 5.0, 6.0)]
  + [{"tpsa": value} for value in (0, 19, 20, 60, 100, 141)]
  + [{"qed": value} for value in (0.0, 0.29, 0.3, 0.49, 0.5, 0.74, 0.75, 1.0)]
  + [{"hbd": 6}, {"hba": 11}, {"hbd": 6, "hba": 11}, {"rotatable_bonds": 11}])
def test_all_legacy_suggestion_branches_remain_descriptor_only(calculator, changes):
    for text in prose_surfaces(calculator, boundary_props(**changes)):
        assert_bounded(text)


def test_descriptor_table_is_separate_from_rule_screening(calculator):
    report = calculator.format_properties("CCO", calculator.calculate_properties("CCO"))
    assert "RDKit 计算描述符" in report
    descriptors, screening = report.split("### 💊 启发式筛选", 1)
    assert "≤ 500" not in descriptors and "≤ 140" not in descriptors
    assert "Lipinski" in screening and "Veber" in screening
    assert "理想" not in report and "优秀" not in report


def test_empty_properties_are_not_reported_as_complete_compliance(calculator):
    for text in prose_surfaces(calculator, {}):
        assert_bounded(text)
        assert "已评估 0/4 项" in text and "已评估 0/2 项" in text
        assert "不能判定全部符合" in text


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "N/A", True])
def test_unusable_rule_input_does_not_count_as_assessed(calculator, value):
    props = boundary_props(logp=value)
    for text in prose_surfaces(calculator, props):
        assert "已评估 3/4 项" in text
        assert "不完整" in text
        assert "不能判定全部符合" in text
