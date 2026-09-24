"""Real RDKit execution, independent numeric baselines and evidence boundaries.

Scalar formula fixtures and injected dependency failures are explicitly synthetic;
no rule result or descriptor is mocked in the real-molecule regressions.
"""
from copy import deepcopy
from itertools import product

import pytest
from rdkit import Chem
from rdkit.Chem import Crippen, Descriptors, Lipinski, QED

from src.agent.contracts import AgentErrorCode, ObservationStatus
from src.agent.tooling import LegacyPythonToolAdapter, RetryPolicy, ToolSpec
from src.agent.tools import base_tool
from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment


MOLECULES = (
    pytest.param("CCO", 0, True, False, id="ethanol"),
    pytest.param("C" * 20, 1, False, False, id="C20"),
    pytest.param("C" * 40, 2, False, False, id="C40"),
    pytest.param("CC(C)NCC(O)COc1cccc2ccccc12", 0, True, True, id="propranolol"),
)
SMILES = tuple(case.values[0] for case in MOLECULES)
RESULT_KEYS = {"query", "success", "message", "data", "formatted", "reasoning"}
UNSUPPORTED = (
    "预测具有良好的口服生物利用度", "通常仍可接受", "可能影响药代动力学性质",
    "全面的药物相似性分析",
)


@pytest.fixture
def tool():
    return DrugLikenessAssessment()


def expected_overall(qed, violations, veber, lead):
    # Independent specification baseline; never call a production scoring helper.
    parts = {"qed": qed, "lipinski": max(0, 1 - violations / 4),
             "veber": 1.0 if veber else 0.5, "lead_likeness": 1.0 if lead else 0.3}
    total = (0.4 * parts["qed"] + 0.3 * parts["lipinski"]
             + 0.2 * parts["veber"] + 0.1 * parts["lead_likeness"])
    grade, category = next(
        (grade, category) for threshold, grade, category in (
            (0.8, "优秀", "高类药性"), (0.6, "良好", "中等类药性"),
            (0.4, "一般", "较低类药性"), (float("-inf"), "较差", "低类药性"),
        ) if total >= threshold
    )
    return {"score": round(total, 3), "grade": grade, "category": category,
            "component_scores": parts}


def rdkit_baseline(smiles):
    mol = Chem.MolFromSmiles(smiles)
    assert mol is not None
    raw = {
        "molecular_weight": Descriptors.MolWt(mol), "logp": Crippen.MolLogP(mol),
        "hba": Lipinski.NumHAcceptors(mol), "hbd": Lipinski.NumHDonors(mol),
        "tpsa": Descriptors.TPSA(mol), "rotatable_bonds": Lipinski.NumRotatableBonds(mol),
        "aromatic_rings": Descriptors.NumAromaticRings(mol),
        "heteroatoms": Lipinski.NumHeteroatoms(mol),
    }
    limits = {"molecular_weight": 500, "logp": 5, "hba": 10, "hbd": 5}
    lip_details = {key: {"value": raw[key], "limit": limit, "pass": raw[key] <= limit}
                   for key, limit in limits.items()}
    violations = [key for key in limits if raw[key] > limits[key]]
    veber_details = {
        key: {"value": raw[key], "limit": limit, "pass": raw[key] <= limit}
        for key, limit in {"tpsa": 140, "rotatable_bonds": 10}.items()
    }
    veber = raw["tpsa"] <= 140 and raw["rotatable_bonds"] <= 10
    mw_pass, logp_pass = 250 <= raw["molecular_weight"] <= 350, 1 <= raw["logp"] <= 3
    qed = QED.qed(mol)
    return {
        "qed_score": round(qed, 3),
        "lipinski_rule_of_five": {
            "violations": violations, "violation_count": len(violations),
            "compliance": len(violations) <= 1, "details": lip_details,
        },
        "veber_rules": {
            "tpsa_compliance": raw["tpsa"] <= 140,
            "rotatable_bonds_compliance": raw["rotatable_bonds"] <= 10,
            "overall_compliance": veber, "details": veber_details,
        },
        "lead_likeness": {
            "molecular_weight_compliance": mw_pass, "logp_compliance": logp_pass,
            "overall_compliance": mw_pass and logp_pass,
            "details": {
                "molecular_weight": {"value": raw["molecular_weight"], "range": "250-350", "pass": mw_pass},
                "logp": {"value": raw["logp"], "range": "1-3", "pass": logp_pass},
            },
        },
        "overall_assessment": expected_overall(qed, len(violations), veber, mw_pass and logp_pass),
        "molecular_properties": {
            **raw, "molecular_weight": round(raw["molecular_weight"], 2),
            "logp": round(raw["logp"], 3), "tpsa": round(raw["tpsa"], 2),
        },
    }


def assert_bounded(text):
    for evidence in ("RDKit", "描述符", "启发式规则", "加权规则评分",
                     "不是成药成功概率", "不能确定口服生物利用度或疗效"):
        assert evidence in text
    assert not any(claim in text for claim in UNSUPPORTED)


@pytest.mark.parametrize("smiles,count,veber,lead", MOLECULES)
def test_real_descriptors_rules_and_result_contract_unchanged(tool, smiles, count, veber, lead):
    result = tool.execute(smiles)
    assert set(result) == RESULT_KEYS
    assert result["success"] is True
    assert result["query"] == smiles
    assert result["message"] == "成功评估了 1 个分子的类药性质"
    assert isinstance(result["data"], list) and len(result["data"]) == 1
    assert all(isinstance(result[key], str) for key in ("message", "formatted", "reasoning"))
    row = result["data"][0]
    assert set(row) == {"smiles", "assessment"} and row["smiles"] == smiles
    assessment, expected = row["assessment"], rdkit_baseline(smiles)
    assert set(assessment) == set(expected)
    assert assessment["lipinski_rule_of_five"]["violation_count"] == count
    assert assessment["veber_rules"]["overall_compliance"] is veber
    assert assessment["lead_likeness"]["overall_compliance"] is lead
    for key in expected.keys() - {"overall_assessment"}:
        assert assessment[key] == expected[key]
    assert set(assessment["overall_assessment"]) == set(expected["overall_assessment"])
    parts = assessment["overall_assessment"]["component_scores"]
    assert set(parts) == {"qed", "lipinski", "veber", "lead_likeness"}
    assert parts["qed"] == QED.qed(Chem.MolFromSmiles(smiles))
    assert parts["qed"] != assessment["qed_score"]  # raw, not display-rounded QED


@pytest.mark.parametrize("smiles,count,veber,lead", MOLECULES)
@pytest.mark.parametrize("component", ["lipinski", "veber", "lead_likeness"])
def test_execute_consumes_rule_count_and_boolean_fields(tool, smiles, count, veber, lead, component):
    result = tool.execute(smiles)
    assert result["success"] is True
    actual = result["data"][0]["assessment"]["overall_assessment"]["component_scores"]
    expected = rdkit_baseline(smiles)["overall_assessment"]["component_scores"]
    assert actual[component] == expected[component]


@pytest.mark.parametrize("smiles,count,veber,lead", MOLECULES)
def test_real_total_grade_and_full_assessment_match_independent_baseline(tool, smiles, count, veber, lead):
    result = tool.execute(smiles)
    assert result["success"] is True
    assert result["data"] == [{"smiles": smiles, "assessment": rdkit_baseline(smiles)}]


@pytest.mark.parametrize("qed,count,veber,lead", list(product((0.0, 0.731234, 1.0), range(6), (False, True), (False, True))))
def test_existing_scalar_formula_weights_floor_and_raw_qed(tool, qed, count, veber, lead):
    # Synthetic scalar inputs exercise every component independent of chemistry.
    assert tool._calculate_overall_assessment(qed, count, veber, lead) == expected_overall(qed, count, veber, lead)


@pytest.mark.parametrize("threshold,qed,count,veber,lead,below,at", [
    (0.4, 0.675, 4, False, False, "较差", "一般"),
    (0.6, 0.25, 0, False, True, "一般", "良好"),
    (0.8, 0.675, 0, True, False, "良好", "优秀"),
])
@pytest.mark.parametrize("offset", [-1e-8, 0.0, 1e-8])
def test_scalar_grade_boundaries_use_unrounded_total(tool, threshold, qed, count, veber, lead, below, at, offset):
    actual = tool._calculate_overall_assessment(qed + offset, count, veber, lead)
    assert actual == expected_overall(qed + offset, count, veber, lead)
    assert actual["score"] == threshold  # same display score on both sides
    assert actual["grade"] == (below if offset < 0 else at)


@pytest.mark.parametrize("smiles,count,veber,lead", MOLECULES)
@pytest.mark.parametrize("surface", ["formatted", "reasoning"])
def test_each_single_output_surface_is_evidence_bounded(tool, smiles, count, veber, lead, surface):
    result = tool.execute(smiles)
    assert result["success"] is True
    assert_bounded(result[surface])


@pytest.mark.parametrize("smiles,count,veber,lead", MOLECULES)
def test_interpretation_reports_exact_rule_count_without_pharmacokinetic_claim(tool, smiles, count, veber, lead):
    assessment = tool.assess_drug_likeness(smiles)
    text = tool._generate_interpretation(smiles, assessment)
    expected = ("未违反所检查的Lipinski阈值" if count == 0 else
                f"违反{count}项所检查的Lipinski阈值，仅作为规则筛选提示")
    assert expected in text
    assert str(assessment["overall_assessment"]["score"]) in text
    assert not any(claim in text for claim in UNSUPPORTED)


@pytest.mark.parametrize("surface", ["formatted", "reasoning"])
def test_batch_preserves_order_and_bounds_every_report_and_reasoning(tool, surface):
    result = tool.execute("\n".join(f"SMILES: {smiles}" for smiles in SMILES))
    assert result["success"] is True
    assert [row["smiles"] for row in result["data"]] == list(SMILES)
    if surface == "reasoning":
        assert_bounded(result["reasoning"])
        return
    reports = result["formatted"].split("💊 类药性评估结果")[1:]
    assert len(reports) == len(SMILES)
    for smiles, report in zip(SMILES, reports):
        assert f"SMILES: `{smiles}`" in report
        assert_bounded(report)


def assert_failed_without_values(result):
    assert set(result) == RESULT_KEYS
    assert result["success"] is False
    assert result["data"] is None
    assert result["formatted"] == ""


@pytest.mark.parametrize("query", ["", "SMILES: CC(C)((", "SMILES: CCO\nSMILES: CC(C)(("])
def test_invalid_input_fails_before_any_calculation(tool, monkeypatch, query):
    def unexpected_calculation(_smiles):
        pytest.fail("Invalid input must not start assessment")
    monkeypatch.setattr(tool, "assess_drug_likeness", unexpected_calculation)
    result = tool.execute(query)
    assert_failed_without_values(result)
    assert result["query"] == query
    assert result["message"] == "SMILES 无效或缺失；请提供完整结构，使用换行或分号分隔，不会提取片段替代。"
    assert result["reasoning"] == "输入校验失败，未进行类药性计算。"


def test_missing_rdkit_retains_unavailable_error_without_values(tool, monkeypatch):
    monkeypatch.setattr(base_tool, "RDKIT_AVAILABLE", False)
    result = tool.execute("CCO")
    assert_failed_without_values(result)
    assert result["message"] == "错误: RDKit未安装。请使用以下命令安装: conda install -c conda-forge rdkit"
    assert result["reasoning"] == ""


@pytest.mark.parametrize("query", ["CCO", "CCO\nCCN"])
def test_rdkit_calculation_failure_is_not_fabricated(tool, monkeypatch, query):
    def broken_qed(_mol):
        raise RuntimeError("controlled QED failure")
    monkeypatch.setattr(QED, "qed", broken_qed)
    result = tool.execute(query)
    assert_failed_without_values(result)
    assert result["message"] == "无法评估类药性质。请检查SMILES结构是否正确。"
    assert result["reasoning"] == "提供的SMILES结构似乎无效或无法被RDKit处理。"


def adapter_for(tool):
    return LegacyPythonToolAdapter(ToolSpec(
        name=tool.name, version="1", description="Drug-likeness evidence regression",
        input_schema=None, output_schema=None, capabilities={"property"},
        timeout_seconds=5.0, retry_policy=RetryPolicy(max_attempts=1),
        side_effects="none", idempotent=True, sensitive_fields=set(),
    ), tool)


@pytest.mark.parametrize("query", ["CCO", "\n".join(SMILES)])
def test_real_tool_adapter_preserves_computed_values_and_bounded_report(tool, query):
    captured = []
    adapted = adapter_for(tool).execute(query, raw_validator=lambda raw: captured.append(deepcopy(raw)))
    assert adapted.success is True and adapted.status == ObservationStatus.SUCCEEDED
    raw, = captured
    assert adapted.data == raw["data"]
    assert adapted.formatted == raw["formatted"] and adapted.message == raw["message"]
    assert adapted.data == [{"smiles": s, "assessment": rdkit_baseline(s)} for s in query.splitlines()]
    assert_bounded(raw["reasoning"])
    assert_bounded(adapted.formatted)
    assert adapted.error is None and isinstance(adapted.elapsed_ms, int)


def test_real_adapter_preserves_upstream_metadata_without_changing_science(tool):
    # Only metadata is synthetic; the wrapped call computes real RDKit results.
    captured = []
    metadata = {
        "warnings": ["unit-test upstream warning"],
        "evidence": [{"source": "unit-test metadata", "method": "RDKit descriptors"}],
        "quality": {"metadata_fixture": True},
        "artifacts": [{"artifact_type": "report", "path": "unit-test/report.json",
                       "label": "metadata fixture only; no file created", "mime_type": "application/json",
                       "metadata": {"fixture": True}}],
    }

    class WithMetadata:
        name = tool.name

        def execute(self, query):
            raw = tool.execute(query)
            captured.append(deepcopy(raw))
            return {**raw, **deepcopy(metadata)}

    adapted = adapter_for(WithMetadata()).execute("CCO")
    raw, = captured
    assert adapted.success is True and adapted.status == ObservationStatus.SUCCEEDED
    assert adapted.data == raw["data"] and adapted.formatted == raw["formatted"]
    assert adapted.message == raw["message"]
    assert adapted.warnings == metadata["warnings"]
    assert adapted.evidence == metadata["evidence"]
    assert adapted.quality == metadata["quality"]
    artifact, = adapted.artifacts
    for key, value in metadata["artifacts"][0].items():
        assert getattr(artifact, key) == value


def test_adapter_keeps_existing_failure_mapping(tool):
    adapted = adapter_for(tool).execute("SMILES: CC(C)((")
    assert adapted.success is False and adapted.status == ObservationStatus.FAILED
    assert adapted.error.code == AgentErrorCode.INTERNAL_ERROR
    assert adapted.data is None and adapted.formatted == ""
