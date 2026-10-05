"""Regression tests for scientific integrity of molecular design operations."""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from src.molecular_design.ai import recommend, validate_fragment_smiles
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


@pytest.mark.parametrize(
    "smiles",
    [
        "[*]C[*]",
        "[*]C.[*]O",
        "[*]=O",
        "C.[*]O",
    ],
)
def test_ai_fragment_validation_requires_one_connected_single_bond_connection(smiles):
    assert validate_fragment_smiles(smiles) is None


def test_ai_fragment_validation_returns_canonical_single_connection_fragment():
    assert validate_fragment_smiles("C[*]") == "*C"


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


@pytest.mark.parametrize(
    "command",
    ["LogP < 3，提高 LogP", "提高 LogP，LogP < 3"],
)
def test_optimization_parser_keeps_one_explicit_constraint_per_metric(command):
    goals = parse_optimization_goals(command)
    assert list(goals) == ["logp"]
    assert goals["logp"]["operator"] == "<"
    assert goals["logp"]["threshold"] == 3


def test_goal_dedup_preserves_distinct_bounds():
    goals = parse_optimization_goals("LogP > 1, LogP < 3, LogP < 3")
    assert len(goals) == 2
    assert not evaluate_goals({"logp": 4}, goals)["summary"]["all_passed"]
    assert not evaluate_goals({"logp": 0}, goals)["summary"]["all_passed"]
    assert evaluate_goals({"logp": 2}, goals)["summary"]["all_passed"]


def test_conflicting_direction_only_goals_require_clarification():
    with pytest.raises(ValueError, match="冲突"):
        parse_optimization_goals("提高 LogP，降低 LogP")


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


def test_fragment_repository_excludes_fragments_that_substitution_rejects(tmp_path):
    csv_path = tmp_path / "fragments.csv"
    pd.DataFrame(
        [
            {"fragment_smiles": "[*]N", "frequency": 3},
            {"fragment_smiles": "[*]C[*]", "frequency": 100},
            {"fragment_smiles": "[*]=O", "frequency": 90},
        ]
    ).to_csv(csv_path, index=False)

    repository = FragmentRepository(csv_path)
    queried = repository.query(page_size=20)
    recommended = repository.recommend_for_command("降低 LogP", limit=20)

    assert [row["fragment_smiles"] for row in queried["fragments"]] == ["[*]N"]
    assert [row["fragment_smiles"] for row in recommended] == ["[*]N"]
    assert repository.query(search="[*]N")["total"] == 1


def test_lower_logp_does_not_create_contradictory_fragment_labels():
    labels = infer_label_filters("降低 LogP", ["label_lipophilic", "label_hydrophilic"])
    assert labels == ["label_hydrophilic"]


def test_lower_logp_takes_precedence_over_generic_lipophilic_wording():
    labels = infer_label_filters(
        "降低 LogP，同时避免亲脂片段",
        ["label_lipophilic", "label_hydrophilic"],
    )
    assert labels == ["label_hydrophilic"]


def test_conflicting_logp_directions_do_not_broaden_fragment_recommendations():
    with pytest.raises(ValueError, match="冲突"):
        infer_label_filters("降低 LogP，提高 LogP", ["label_hydrophilic", "label_lipophilic"])


def test_inferred_fragment_labels_are_unique():
    assert infer_label_filters("降低 LogP，增加亲水性", ["label_hydrophilic"]) == ["label_hydrophilic"]


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


def test_llm_reply_only_mentions_fragments_that_pass_validation():
    class Model:
        def generate(self, prompt, **kwargs):
            return (
                '{"fragments": ['
                '{"fragment_smiles": "[*]N", "name": "valid"},'
                '{"fragment_smiles": "[*]C[*]", "name": "invalid"}'
                ']}'
            )

    result = asyncio.run(
        recommend(
            model=Model(),
            command="降低 LogP",
            current_smiles="c1ccccc1[*]",
            current_props={},
            recommended_fragments=[],
        )
    )

    assert result["recommendation_mode"] == "llm"
    assert "*N" in result["reply"]
    assert "*C*" not in result["reply"]


def test_recommendation_merge_rejects_invalid_repository_fragments():
    result = asyncio.run(
        recommend(
            model=None,
            command="降低 LogP",
            current_smiles="c1ccccc1[*]",
            current_props={},
            recommended_fragments=[
                {"fragment_smiles": "[*]C[*]", "source": "local_rule"},
                {"fragment_smiles": "[*]N", "source": "local_rule"},
            ],
        )
    )

    assert [item["fragment_smiles"] for item in result["recommended_fragments"]] == ["*N"]
    assert "[*]C[*]" not in result["reply"]


def test_library_dependency_failure_is_not_cached_as_empty(tmp_path, monkeypatch):
    import src.molecular_design.fragments as fragments

    path = tmp_path / "fragments.csv"
    pd.DataFrame([{"fragment_smiles": "[*]N", "dummy_atoms": 9}]).to_csv(path, index=False)
    repository = FragmentRepository(path)
    validator = fragments.canonicalize_connection_fragment

    def unavailable(smiles):
        raise ImportError("RDKit unavailable")

    monkeypatch.setattr(fragments, "canonicalize_connection_fragment", unavailable)
    with pytest.raises(ImportError):
        repository.query()
    monkeypatch.setattr(fragments, "canonicalize_connection_fragment", validator)
    assert repository.query()["total"] == 1


def test_recommendation_library_validation_runs_off_event_loop(tmp_path):
    import threading
    from src.molecular_design.service import MolecularDesignService

    request_thread = threading.get_ident()
    called_on = []

    class Repository:
        def recommend_for_command(self, command):
            called_on.append(threading.get_ident())
            return []

    service = MolecularDesignService(
        tmp_path / "absent.csv", tmp_path, fragment_repository=Repository()
    )
    asyncio.run(service.ai_recommend("降低 LogP", "CCO", {}))
    assert len(called_on) == 1
    assert called_on[0] != request_thread


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
