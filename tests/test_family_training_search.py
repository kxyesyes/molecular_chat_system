"""Tests for multi-seed family-model search and selection."""

import importlib.util
from pathlib import Path

import pytest


def module():
    path = Path(__file__).parents[1] / "scripts" / "search_family_activity_models.py"
    spec = importlib.util.spec_from_file_location("family_training_search", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def job(family, task, *, seed, validation, test, model_id=None):
    return {
        "family": family,
        "task": task,
        "status": "completed",
        "model_id": model_id or f"{family}-{task}-{seed}",
        "validation_metrics": validation,
        "test_metrics": test,
        "test_baseline": {
            "roc_auc": 0.5,
            "pr_auc": 0.5,
            "balanced_accuracy": 0.5,
        } if task == "classification" else {"rmse": 2.0, "mae": 2.0, "r2": 0.0},
        "quality_assessment": {"status": "improves_all_metrics"},
    }


def report(name, options, jobs):
    return {"run_id": name, "status": "completed", "activated": False,
            "options": options, "jobs": jobs, "bundles": [f"{name}-pde-family", f"{name}-buche-family"]}


def test_selection_groups_seeds_and_uses_validation_not_test():
    search = module()
    base = dict(epochs=50, patience=12, batch_size=32, lr=.001, num_layers=5,
                hidden_size=256, dropout=.2, weight_decay=.0001,
                lr_scheduler="Cosine", loss_metric="MSE")
    seed_one_jobs = [job("pde-family", "regression", seed=1,
        validation={"rmse": 0.8, "mae": 0.6, "r2": 0.7},
        test={"rmse": 99, "mae": 99, "r2": -99})]
    seed_two_jobs = [job("pde-family", "regression", seed=2,
        validation={"rmse": 0.9, "mae": 0.7, "r2": 0.6},
        test={"rmse": 0.1, "mae": 0.1, "r2": 0.99})]
    for family in ("pde-family", "buche-family"):
        for task in ("classification", "regression"):
            if (family, task) != ("pde-family", "regression"):
                seed_one_jobs.append(job(family, task, seed=1,
                    validation={"pr_auc": .6, "roc_auc": .6, "balanced_accuracy": .6}
                    if task == "classification" else {"rmse": 1., "mae": 1., "r2": .1},
                    test={"pr_auc": .6, "roc_auc": .6, "balanced_accuracy": .6}
                    if task == "classification" else {"rmse": 1., "mae": 1., "r2": .1}))
    reports = [report("seed-1", {**base, "random_seed": 1}, seed_one_jobs),
               report("seed-2", {**base, "random_seed": 2}, seed_two_jobs)]
    selected = search.select_models(reports)
    chosen = selected["selections"]["pde-family/regression"]
    assert chosen["run_id"] == "seed-1"
    assert chosen["selection_basis"] == "validation_metrics"
    assert chosen["test_metrics"]["rmse"] == 99
    assert chosen["stability"]["sample_count"] == 2


def test_validation_only_reports_can_be_selected_without_test_metrics():
    search = module()
    options = dict(search.BASE_OPTIONS, random_seed=42)
    reports = [report("validation-only", options, [
        job("pde-family", "classification", seed=42,
            validation={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7},
            test={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7}),
        job("pde-family", "regression", seed=42,
            validation={"rmse": .8, "mae": .6, "r2": .7},
            test={"rmse": .8, "mae": .6, "r2": .7}),
        job("buche-family", "classification", seed=42,
            validation={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7},
            test={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7}),
        job("buche-family", "regression", seed=42,
            validation={"rmse": .8, "mae": .6, "r2": .7},
            test={"rmse": .8, "mae": .6, "r2": .7}),
    ])]
    for item in reports[0]["jobs"]:
        item.pop("test_metrics")
    selected = search.select_models(reports)
    assert all(value["test_metrics"] is None for value in selected["selections"].values())


def test_selection_requires_four_completed_jobs_and_never_activates():
    search = module()
    options = dict(search.BASE_OPTIONS, random_seed=42)
    incomplete = report("bad", options, [job("pde-family", "classification", seed=42,
        validation={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7},
        test={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7})])
    with pytest.raises(ValueError, match="four completed jobs"):
        search.select_models([incomplete])

    selected = search.select_models([report("good", options, [
        job("pde-family", "classification", seed=42,
            validation={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7},
            test={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7}),
        job("pde-family", "regression", seed=42,
            validation={"rmse": .8, "mae": .6, "r2": .7},
            test={"rmse": .8, "mae": .6, "r2": .7}),
        job("buche-family", "classification", seed=42,
            validation={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7},
            test={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7}),
        job("buche-family", "regression", seed=42,
            validation={"rmse": .8, "mae": .6, "r2": .7},
            test={"rmse": .8, "mae": .6, "r2": .7}),
    ])])
    assert selected["activated"] is False
    assert len(selected["selections"]) == 4


def test_invalid_trial_options_are_rejected():
    search = module()
    with pytest.raises(ValueError):
        search.validate_trial({"name": "bad", "random_seed": -1})
    with pytest.raises(ValueError):
        search.validate_trial({"name": "bad", "random_seed": 1, "unknown": 2})


def test_single_seed_stability_std_is_json_safe_and_unstable():
    search = module()
    options = dict(search.BASE_OPTIONS, random_seed=42)
    reports = [report("one-seed", options, [
        job("pde-family", "classification", seed=42,
            validation={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7},
            test={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7}),
        job("pde-family", "regression", seed=42,
            validation={"rmse": .8, "mae": .6, "r2": .7},
            test={"rmse": .8, "mae": .6, "r2": .7}),
        job("buche-family", "classification", seed=42,
            validation={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7},
            test={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7}),
        job("buche-family", "regression", seed=42,
            validation={"rmse": .8, "mae": .6, "r2": .7},
            test={"rmse": .8, "mae": .6, "r2": .7}),
    ])]
    selected = search.select_models(reports)
    for value in selected["selections"].values():
        assert value["stability"]["validation_primary_std"] is None
        assert value["stability"]["stable"] is False


def test_validation_only_partial_report_with_all_jobs_completed_is_recoverable():
    search = module()
    options = dict(search.BASE_OPTIONS, random_seed=42, evaluation_mode="validation_only")
    jobs = [
        job("pde-family", "classification", seed=42,
            validation={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7},
            test={}),
        job("pde-family", "regression", seed=42,
            validation={"rmse": .8, "mae": .6, "r2": .7}, test={}),
        job("buche-family", "classification", seed=42,
            validation={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7},
            test={}),
        job("buche-family", "regression", seed=42,
            validation={"rmse": .8, "mae": .6, "r2": .7}, test={}),
    ]
    recovered = report("validation-only-partial", options, jobs)
    recovered["status"] = "partial"
    selected = search.select_models([recovered])
    assert len(selected["selections"]) == 4


def test_candidate_only_recovery_excludes_comparison_reports():
    search = module()
    candidate = {"source": "candidate", "report": {"run_id": "candidate"}}
    fresh = {"source": "new", "report": {"run_id": "fresh"}}
    comparison = {"source": "existing", "report": {"run_id": "r2"}}
    assert [item["run_id"] for item in search._selection_reports(
        [candidate, fresh, comparison])] == ["candidate", "fresh"]


def test_chain_summary_accepts_real_outputs_and_counts_review_mismatches():
    search = module()
    rows = [
        {"activity_probability": .8, "predicted_pIC50": 6.0,
         "classification_regression_consistent": True},
        {"activity_probability": .2, "predicted_pIC50": 6.0,
         "classification_regression_consistent": False},
    ]
    summary = search._summarize_chain_rows(rows)
    assert summary["status"] == "passed"
    assert summary["observed_rows"] == 2
    assert summary["consistent_rows"] == 1
    assert summary["review_rows"] == 1


def test_chain_uses_independent_smoke_inputs_instead_of_test_split():
    search = module()
    assert search._CHAIN_SMILES == ("CCO", "CCN")


def test_deployment_gate_requires_stability_baseline_and_current_candidate():
    search = module()
    options = dict(search.BASE_OPTIONS, random_seed=42)
    reports = [report("selected", options, [
        job("pde-family", "classification", seed=42,
            validation={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7},
            test={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7}),
        job("pde-family", "regression", seed=42,
            validation={"rmse": .8, "mae": .6, "r2": .7},
            test={"rmse": .8, "mae": .6, "r2": .7}),
        job("buche-family", "classification", seed=42,
            validation={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7},
            test={"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7}),
        job("buche-family", "regression", seed=42,
            validation={"rmse": .8, "mae": .6, "r2": .7},
            test={"rmse": .8, "mae": .6, "r2": .7}),
    ])]
    selected = search.select_models(reports)
    for key, item in selected["selections"].items():
        task = item["task"]
        metrics = {"pr_auc": .8, "roc_auc": .8, "balanced_accuracy": .7} if task == "classification" else {"rmse": .8, "mae": .6, "r2": .7}
        item["stability"]["stable"] = True
        item["test_baseline"] = {"roc_auc": .5, "pr_auc": .5, "balanced_accuracy": .5} if task == "classification" else {"rmse": 2., "mae": 2., "r2": 0.}
        item["test_stability"] = {"stable": True, "metrics": {
            name: {"worst": value, "std": 0.01} for name, value in metrics.items()
        }}
        item["test_evaluations"] = [{"test_metrics": metrics}]
    current = []
    for key, item in selected["selections"].items():
        family, task = key.split("/")
        current.append({"run_id": "pde-buche-20261006-r2", "family": family,
                        "task": task, "test_metrics": {
                            "pr_auc": .7, "roc_auc": .7, "balanced_accuracy": .6}
                        if task == "classification" else {"rmse": 1., "mae": .9, "r2": .5}})
    gate = search._deployment_gate(selected, current)
    assert gate["eligible"] is True
    selected["selections"]["pde-family/regression"]["test_stability"]["metrics"]["rmse"]["worst"] = 1.2
    assert search._deployment_gate(selected, current)["eligible"] is False
