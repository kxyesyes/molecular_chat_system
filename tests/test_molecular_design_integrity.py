"""Regression tests for scientific integrity of molecular design operations."""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from src.molecular_design.ai import recommend
from src.molecular_design.chemistry import calculate_properties, substitute_fragment
from src.molecular_design.fragments import FragmentRepository, infer_label_filters
from src.molecular_design.optimizer import evaluate_goals, parse_optimization_goals
from src.molecular_design.storage import DesignStorage


def test_substitution_requires_one_explicit_single_connection_point():
    with pytest.raises(ValueError, match="一个.*连接点|连接点"):
        substitute_fragment("c1ccccc1", "[*]C")

    with pytest.raises(ValueError, match="一个.*连接点|连接点"):
        substitute_fragment("c1cc([*])c([*])cc1", "[*]C")

    with pytest.raises(ValueError, match="单键|连接点"):
        substitute_fragment("c1cc([*]=O)ccc1", "[*]C")


def test_substitution_removes_all_dummies_and_preserves_a_valid_product():
    result = substitute_fragment("c1cc([*])ccc1", "[*]C")
    assert result["success"] is True
    assert "*" not in result["new_smiles"]
    assert result["new_smiles"] == "Cc1ccccc1"


def test_optimization_goals_are_empty_without_explicit_user_targets():
    assert parse_optimization_goals("") == {}
    assert parse_optimization_goals("请分析这个分子") == {}
    assert evaluate_goals({"qed": 0.9, "mw": 100})["items"] == []


def test_optimization_parser_handles_html_comparators_negative_values_and_no_duplicates():
    goals = parse_optimization_goals("LogP &lt;= -1.5，MW &gt; 250，QED &gt;= 0.7")
    items = list(goals.values())
    assert [(item["metric"], item["threshold"]) for item in items] == [
        ("logp", -1.5),
        ("mw", 250.0),
        ("qed", 0.7),
    ]
    assert len(items) == 3


def test_optimization_parser_does_not_let_loose_match_overwrite_explicit_match():
    goals = parse_optimization_goals("提高 QED，并且 MW < 300，LogP <= 3")
    assert goals["mw"]["threshold"] == 300
    assert goals["logp"]["threshold"] == 3
    assert goals["qed"]["threshold"] is None
    assert goals["qed"]["direction"] == "max"


def test_fragment_search_treats_user_text_as_literal(tmp_path):
    csv_path = tmp_path / "fragments.csv"
    pd.DataFrame(
        [
            {"fragment_smiles": "[*]C(=O)O", "frequency": 2},
            {"fragment_smiles": "[*]N", "frequency": 1},
        ]
    ).to_csv(csv_path, index=False)
    result = FragmentRepository(csv_path).query(search="[z")
    assert result["success"] is True
    assert result["fragments"] == []


def test_lower_logp_does_not_create_contradictory_fragment_labels():
    labels = infer_label_filters("降低 LogP", ["label_lipophilic", "label_hydrophilic"])
    assert labels == ["label_hydrophilic"]


@pytest.mark.parametrize("command, expected", [
    ("LogP &lt; 3", False), ("LogP &gt; 3", False),
    ("LogP &lt;= 3", True), ("LogP &gt;= 3", True),
    ("LogP 小于 3", False), ("LogP 大于 3", False),
    ("LogP 不高于 3", True), ("LogP 不低于 3", True),
])
def test_goal_boundaries_preserve_comparison_operator(command, expected):
    result = evaluate_goals({"logp": 3}, parse_optimization_goals(command))
    assert len(result["items"]) == 1
    assert result["items"][0]["passed"] is expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), None])
def test_nonfinite_goals_are_unavailable(value):
    item = evaluate_goals({"logp": value}, parse_optimization_goals("LogP < 3"))["items"][0]
    assert item["available"] is False
    assert item["passed"] is None


def test_properties_expose_full_molecule_similarity_with_explicit_name():
    result = calculate_properties("CCO", reference_smiles="CCN")
    assert "morgan_similarity" in result["properties"]
    assert "scaffold_similarity" not in result["properties"]


def test_recommendation_mode_distinguishes_llm_from_local_rules():
    class FakeModel:
        def generate(self, prompt, **kwargs):
            return '{"fragments": [{"fragment_smiles": "[*]O", "name": "羟基"}]}'

    llm_result = asyncio.run(
        recommend(
            model=FakeModel(),
            command="降低 LogP",
            current_smiles="c1ccccc1[*]",
            current_props={},
            recommended_fragments=[],
        )
    )
    local_result = asyncio.run(
        recommend(
            model=None,
            command="降低 LogP",
            current_smiles="c1ccccc1[*]",
            current_props={},
            recommended_fragments=[{"fragment_smiles": "[*]N", "source": "local_rule"}],
        )
    )

    assert llm_result["recommendation_mode"] == "llm"
    assert local_result["recommendation_mode"] == "local_rule"


def test_invalid_llm_payload_uses_explicit_local_rule_fallback():
    class InvalidModel:
        def generate(self, prompt, **kwargs):
            return "这是一段不能作为结构化片段依据的自然语言。"

    result = asyncio.run(
        recommend(
            model=InvalidModel(),
            command="降低 LogP",
            current_smiles="c1ccccc1[*]",
            current_props={},
            recommended_fragments=[{"fragment_smiles": "[*]N", "source": "local_rule"}],
        )
    )
    assert result["recommendation_mode"] == "local_rule"
    assert result["fallback_used"] is True
    assert "规则推荐模式" in result["reply"]


def test_storage_uses_fixed_history_columns_and_rejects_invalid_smiles(tmp_path):
    storage = DesignStorage(tmp_path)
    filename, content = storage.export_history_csv(
        [{"step": 1, "smi": "CCO", "props": {"qed": 0.4}}]
    )
    assert filename.endswith(".csv")
    assert list(pd.read_csv(pd.io.common.StringIO(content)).columns) == [
        "step",
        "smiles",
        "prop_mw",
        "prop_logp",
        "prop_qed",
        "prop_tpsa",
        "prop_hbd",
        "prop_hba",
        "prop_rotbonds",
        "prop_sa_score",
        "prop_morgan_similarity",
    ]
    with pytest.raises(ValueError, match="无效|SMILES"):
        storage.export_history_csv([{"step": 2, "smi": "not a smiles", "props": {}}])


def test_storage_rejects_nonfinite_supplied_properties(tmp_path):
    storage = DesignStorage(tmp_path)
    with pytest.raises(ValueError, match="有限数值"):
        storage.save_molecule("CCO", {"mw": float("nan")})


def test_storage_rejects_properties_from_a_different_smiles(tmp_path):
    storage = DesignStorage(tmp_path)
    with pytest.raises(ValueError, match="不一致"):
        storage.save_molecule("CCO", {"mw": 30.0})
