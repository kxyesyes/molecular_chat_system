"""Run and select a multi-seed PDE/BuChE family-model search.

The underlying training runner remains deliberately frozen.  This module only
orchestrates independent runs, selects from validation metrics, and writes an
unactivated search report.  It never uses held-out test metrics for selection
and never changes the production family bundle.
"""
from __future__ import annotations

import argparse
import copy
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys


ROOT = Path(__file__).resolve().parents[1]
BASE_OPTIONS = dict(
    epochs=50,
    patience=12,
    batch_size=32,
    lr=0.001,
    num_layers=5,
    hidden_size=256,
    dropout=0.2,
    weight_decay=0.0001,
    lr_scheduler="Cosine",
    loss_metric="MSE",
)
FAMILIES = ("pde-family", "buche-family")
TASKS = ("classification", "regression")
_CHAIN_SMILES = ("CCO", "CCN")
_ALLOWED_OPTIONS = set(BASE_OPTIONS) | {"random_seed"}
_DEFAULT_TRIALS = (
    {"name": "baseline-seed-71", "random_seed": 71},
    {"name": "baseline-seed-123", "random_seed": 123},
    {"name": "regularized-seed-71", "random_seed": 71, "lr": 0.0005, "dropout": 0.1},
    {"name": "regularized-seed-123", "random_seed": 123, "lr": 0.0005, "dropout": 0.1},
)


def validate_trial(trial: dict) -> dict:
    if not isinstance(trial, dict) or not isinstance(trial.get("name"), str):
        raise ValueError("trial name is required")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}", trial["name"]):
        raise ValueError("invalid trial name")
    unknown = set(trial) - (_ALLOWED_OPTIONS | {"name"})
    if unknown:
        raise ValueError("unknown trial options")
    seed = trial.get("random_seed")
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("random_seed must be a uint32 integer")
    merged = dict(BASE_OPTIONS, **{key: value for key, value in trial.items() if key != "name"})
    if type(merged["epochs"]) is not int or not 1 <= merged["epochs"] <= 1000:
        raise ValueError("epochs is invalid")
    if type(merged["patience"]) is not int or not 1 <= merged["patience"] <= merged["epochs"]:
        raise ValueError("patience is invalid")
    for key in ("lr", "dropout", "weight_decay"):
        if type(merged[key]) not in (int, float) or merged[key] < 0:
            raise ValueError(f"{key} is invalid")
    return merged


def _runner_module():
    path = Path(__file__).with_name("train_family_activity_models.py")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("family_training_runner_for_search", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _completed_rows(reports):
    rows = []
    for report in reports:
        if not isinstance(report, dict):
            continue
        options = report.get("options")
        if not isinstance(options, dict):
            continue
        jobs = report.get("jobs")
        validation_only_recovery = (
            report.get("status") == "partial"
            and options.get("evaluation_mode") == "validation_only"
            and isinstance(jobs, list)
            and len(jobs) == len(FAMILIES) * len(TASKS)
            and all(isinstance(job, dict) and job.get("status") == "completed" for job in jobs)
        )
        if report.get("status") != "completed" and not validation_only_recovery:
            continue
        for job in jobs:
            if (not isinstance(job, dict) or job.get("status") != "completed"
                    or job.get("family") not in FAMILIES or job.get("task") not in TASKS
                    or (not isinstance(job.get("model_id"), str)
                        and not isinstance(job.get("checkpoint_file"), str))):
                continue
            if not isinstance(job.get("validation_metrics"), dict):
                continue
            rows.append({"run_id": report.get("run_id"), "options": options, "job": job})
    return rows


def _metric_tuple(task, metrics):
    if task == "classification":
        keys = ("pr_auc", "roc_auc", "balanced_accuracy")
        return tuple(float(metrics[key]) for key in keys)
    return tuple(-float(metrics[key]) for key in ("rmse", "mae")) + (float(metrics["r2"]),)


def _group_key(options):
    return json.dumps({key: options[key] for key in sorted(options) if key != "random_seed"},
                      sort_keys=True, separators=(",", ":"))


def select_models(reports):
    rows = _completed_rows(reports)
    by_key = defaultdict(list)
    for row in rows:
        job = row["job"]
        by_key[f"{job['family']}/{job['task']}"].append(row)
    expected = {f"{family}/{task}" for family in FAMILIES for task in TASKS}
    missing = expected - set(by_key)
    if missing:
        raise ValueError("search requires four completed jobs: " + ", ".join(sorted(missing)))

    selections = {}
    for key in sorted(expected):
        task_rows = by_key[key]
        groups = defaultdict(list)
        for row in task_rows:
            groups[_group_key(row["options"])].append(row)
        candidates = []
        for config_key, group in groups.items():
            scores = [_metric_tuple(task_rows[0]["job"]["task"], row["job"]["validation_metrics"])[0]
                      for row in group]
            mean_score = statistics.fmean(scores)
            std_score = statistics.pstdev(scores) if len(scores) > 1 else float("inf")
            stable = len(scores) >= 2 and std_score <= 0.05
            representative = max(
                group,
                key=lambda row: _metric_tuple(row["job"]["task"], row["job"]["validation_metrics"]),
            )
            candidates.append((stable, mean_score, -std_score if std_score != float("inf") else float("-inf"),
                              _metric_tuple(representative["job"]["task"], representative["job"]["validation_metrics"]),
                              config_key, group, representative, std_score))
        stable_candidates = [candidate for candidate in candidates if candidate[0]]
        pool = stable_candidates or candidates
        chosen = max(pool, key=lambda candidate: candidate[:5])
        _, mean_score, _, _, config_key, group, representative, std_score = chosen
        job = representative["job"]
        selections[key] = {
            "run_id": representative["run_id"],
            "model_id": job.get("model_id"),
            "family": job["family"],
            "task": job["task"],
            "options": representative["options"],
            "selection_basis": "validation_metrics",
            "validation_metrics": job["validation_metrics"],
            "test_metrics": job.get("test_metrics"),
            "test_baseline": job.get("test_baseline"),
            "quality_assessment": job.get("quality_assessment"),
            "checkpoint_file": job.get("checkpoint_file"),
            "checkpoint_sha256": job.get("checkpoint_sha256"),
            "stability": {
                "sample_count": len(group),
                "validation_primary_mean": mean_score,
                "validation_primary_std": std_score if math.isfinite(std_score) else None,
                "stable": bool(chosen[0]),
            },
            "candidate_runs": sorted({row["run_id"] for row in group}),
            "candidate_checkpoints": [
                {
                    "run_id": row["run_id"],
                    "checkpoint_file": row["job"].get("checkpoint_file"),
                    "checkpoint_sha256": row["job"].get("checkpoint_sha256"),
                    "random_seed": row["options"].get("random_seed"),
                    "validation_metrics": row["job"]["validation_metrics"],
                }
                for row in sorted(group, key=lambda item: item["options"].get("random_seed", -1))
            ],
            "activated": False,
        }
    return {"activated": False, "selections": selections}


def _load_report(run_id, root=ROOT):
    path = root / "outputs" / "activity_training" / run_id / "report.json"
    if not path.is_file():
        raise FileNotFoundError(f"training report not found: {run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _selection_reports(candidates):
    """Return only reports eligible for current validation-based selection."""
    return [item["report"] for item in candidates
            if item.get("source") in {"new", "candidate"}]


def _test_metric_summary(task, metrics_list):
    if not metrics_list:
        raise ValueError("No final test metrics to summarize")
    keys = ("roc_auc", "pr_auc", "balanced_accuracy") if task == "classification" else ("rmse", "mae", "r2")
    summary = {}
    for key in keys:
        values = [float(metrics[key]) for metrics in metrics_list]
        mean = statistics.fmean(values)
        std = statistics.pstdev(values) if len(values) > 1 else None
        worst = min(values) if task == "classification" or key == "r2" else max(values)
        summary[key] = {
            "mean": mean,
            "std": std,
            "worst": worst,
            "sample_count": len(values),
        }
    return summary


def _summarize_chain_rows(rows):
    """Validate the paired output contract without turning disagreement into failure."""
    if not isinstance(rows, list) or not rows:
        return {"status": "failed", "observed_rows": 0, "consistent_rows": 0,
                "review_rows": 0, "invalid_rows": 0}
    consistent = review = invalid = 0
    for row in rows:
        probability = row.get("activity_probability") if isinstance(row, dict) else None
        value = row.get("predicted_pIC50") if isinstance(row, dict) else None
        flag = row.get("classification_regression_consistent") if isinstance(row, dict) else None
        valid = (type(probability) in (int, float) and math.isfinite(float(probability))
                 and 0 <= probability <= 1
                 and type(value) in (int, float) and math.isfinite(float(value))
                 and type(flag) is bool
                 and flag == ((probability >= 0.5) == (value >= 5.0)))
        if not valid:
            invalid += 1
        elif flag:
            consistent += 1
        else:
            review += 1
    return {"status": "passed" if invalid == 0 else "failed",
            "observed_rows": len(rows), "consistent_rows": consistent,
            "review_rows": review, "invalid_rows": invalid}


class _ReadOnlyBundleRegistry:
    """Expose newly registered bundles to the predictor without activating them."""

    def __init__(self, registry, bundles):
        self._registry = registry
        self._bundles = {key: copy.deepcopy(value) for key, value in bundles.items()}

    def get_active_family_bundle(self, family):
        from src.activity.family_contract import resolve_activity_family
        bundle = self._bundles.get(resolve_activity_family(family))
        return copy.deepcopy(bundle) if bundle is not None else None

    def resolve_weights(self, model_id):
        return self._registry.resolve_weights(model_id)


def _run_model_chain(root, package_version, registry, bundles):
    """Run the real paired family predictor against each fixed scaffold test set."""
    from src.activity.family_predictor import FamilyActivityPredictor
    predictor = FamilyActivityPredictor(_ReadOnlyBundleRegistry(registry, bundles))
    family_results = {}
    try:
        for family in FAMILIES:
            rows = predictor.predict(list(_CHAIN_SMILES), target=family)
            summary = _summarize_chain_rows(rows)
            bundle = bundles[family]
            expected_models = {task: bundle["models"][task]["model_id"] for task in TASKS}
            identity_ok = len(rows) == len(_CHAIN_SMILES) and all(
                row.get("provenance", {}).get("bundle_id") == bundle["bundle_id"]
                and all(row.get("provenance", {}).get("models", {}).get(task, {}).get("model_id")
                        == model_id for task, model_id in expected_models.items())
                for row in rows)
            summary.update(family=family, bundle_id=bundle["bundle_id"],
                           input_sample_count=len(_CHAIN_SMILES),
                           input_scope="independent_smoke_inputs",
                           identity_verified=identity_ok,
                           activated=False)
            if not identity_ok:
                summary["status"] = "failed"
            family_results[family] = summary
    finally:
        predictor._cache.clear()
    return {"status": "passed" if family_results and all(
        item["status"] == "passed" and item["identity_verified"]
        for item in family_results.values()) else "failed",
            "families": family_results, "activated": False}


def _stable_test_metrics(task, summary, threshold=0.05):
    keys = ("roc_auc", "pr_auc", "balanced_accuracy") if task == "classification" else ("rmse", "mae", "r2")
    return all(summary[key]["std"] is not None and summary[key]["std"] <= threshold for key in keys)


def _resolve_candidate_checkpoint(root, run_id, relative_path):
    if not isinstance(relative_path, str) or not relative_path.startswith("checkpoints/"):
        raise ValueError("Invalid candidate checkpoint reference")
    run_dir = (root / "outputs" / "activity_training" / run_id).resolve(strict=True)
    candidate = (run_dir / relative_path).resolve(strict=True)
    if not candidate.is_relative_to(run_dir) or not candidate.is_file():
        raise ValueError("Candidate checkpoint is outside its run")
    return candidate


def _finalize_selected_models(root, search_id, package_version, selected, new_reports):
    """Evaluate selected groups once on test, publish representatives, bind bundles."""
    import torch
    from src.activity.family_dataset import load_family_dataset
    from src.activity.model_registry import ActivityModelRegistry
    from src.activity.prepared_training import evaluate_checkpoint, publish_model

    runner = _runner_module()
    stability_threshold = 0.05
    descriptors = {}
    manifests = {}
    bundle_records = {}
    for family in FAMILIES:
        family_path = root / "data" / "activity" / "prepared" / f"{family}-{package_version}" / "family_dataset.json"
        descriptors[family] = load_family_dataset(family_path)
        for task in TASKS:
            manifests[f"{family}/{task}"] = family_path.parent / descriptors[family]["datasets"][task]["path"]

    final_models_dir = root / "data" / "activity" / "models" / search_id
    final_models_dir.mkdir(parents=True, exist_ok=False)
    evaluations = {}
    with runner._process_overrides(final_models_dir):
        registry = ActivityModelRegistry(final_models_dir)
        device = torch.device("cuda")
        for key, selection in selected["selections"].items():
            family, task = key.split("/", 1)
            candidate_evaluations = []
            for candidate in selection["candidate_checkpoints"]:
                checkpoint = _resolve_candidate_checkpoint(
                    root, candidate["run_id"], candidate["checkpoint_file"])
                digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
                if digest != candidate["checkpoint_sha256"]:
                    raise ValueError("Candidate checkpoint SHA-256 mismatch")
                evaluated = evaluate_checkpoint(checkpoint, manifests[key], device)
                candidate_evaluations.append({
                    "run_id": candidate["run_id"],
                    "random_seed": candidate["random_seed"],
                    "checkpoint_file": candidate["checkpoint_file"],
                    "checkpoint_sha256": digest,
                    "validation_metrics": evaluated["validation_metrics"],
                    "test_metrics": evaluated["test_metrics"],
                    "test_prediction_summary": evaluated["test_prediction_summary"],
                    "evaluated": evaluated,
                })
            if not candidate_evaluations:
                raise ValueError(f"No checkpoints available for {key}")
            test_metrics = [item["test_metrics"] for item in candidate_evaluations]
            summary = _test_metric_summary(task, test_metrics)
            baseline_prepared = candidate_evaluations[0]["evaluated"]["prepared"]
            baseline = runner.baseline_metrics(
                task,
                baseline_prepared.frames["train"]["normalized_value"].tolist(),
                baseline_prepared.frames["test"]["normalized_value"].tolist(),
            )
            representative_seed = selection["options"]["random_seed"]
            representative = next(
                item for item in candidate_evaluations
                if item["random_seed"] == representative_seed
            )
            result = {
                "best_epoch": representative["evaluated"]["best_epoch"],
                "epochs_completed": representative["evaluated"]["epochs_completed"],
                "validation_metrics": representative["validation_metrics"],
                "test_metrics": representative["test_metrics"],
                "test_prediction_summary": representative["test_prediction_summary"],
            }
            metadata = publish_model(
                f"{search_id}-final-{family}-{task}",
                representative["evaluated"]["model"],
                representative["evaluated"]["prepared"],
                representative["evaluated"]["model_config"],
                result,
                representative_seed,
                code_provenance=representative["evaluated"]["training_code"],
            )
            quality = runner.assess_quality(task, representative["test_metrics"], baseline)
            selection.update({
                "model_id": metadata["model_id"],
                "test_metrics": representative["test_metrics"],
                "test_baseline": baseline,
                "quality_assessment": quality,
                "final_model_artifacts": {
                    "weights_file": metadata["weights_file"],
                    "weights_sha256": metadata["weights_sha256"],
                    "model_card_file": metadata["model_card_file"],
                    "model_card_sha256": metadata["model_card_sha256"],
                    "model_registry_dir": f"data/activity/models/{search_id}",
                },
                "test_stability": {
                    "threshold": stability_threshold,
                    "stable": _stable_test_metrics(task, summary, stability_threshold),
                    "metrics": summary,
                },
                "test_evaluations": [
                    {key: value for key, value in item.items() if key != "evaluated"}
                    for item in candidate_evaluations
                ],
            })
            evaluations[key] = candidate_evaluations
        for family in FAMILIES:
            family_key = f"{search_id}-{family}"
            bundle = registry.register_family_bundle(
                bundle_id=family_key,
                family_dataset_path=root / "data" / "activity" / "prepared" / f"{family}-{package_version}" / "family_dataset.json",
                classification_model_id=selected["selections"][f"{family}/classification"]["model_id"],
                regression_model_id=selected["selections"][f"{family}/regression"]["model_id"],
            )
            bundle_records[family] = bundle
            selected.setdefault("final_bundles", []).append(bundle["bundle_id"])
        selected["classification_regression_chain"] = _run_model_chain(
            root, package_version, registry, bundle_records)
    return selected


def _ensure_random_diagnostic_packages(root):
    from src.activity import dataset_contract as dc
    from src.activity.family_dataset import load_family_dataset, prepare_family_dataset

    prepared_root = root / "data" / "activity" / "prepared"
    descriptors = {}
    for family in FAMILIES:
        # v2 groups stereochemical variants by the model's non-stereo feature
        # identity; v1 packages generated before that guard are not reusable.
        package_id = f"{family}-random-v2"
        package_dir = prepared_root / package_id
        descriptor_path = package_dir / "family_dataset.json"
        if descriptor_path.is_file():
            descriptor = load_family_dataset(descriptor_path)
            if descriptor["split"]["algorithm"] != dc._RANDOM_SPLIT_ALGORITHM:
                raise ValueError("Existing random diagnostic package uses the wrong split algorithm")
        else:
            source = prepared_root / f"{family}-v2" / "source.csv"
            prepare_family_dataset(
                source,
                family=family,
                package_id=package_id,
                output_dir=prepared_root,
                validate_only=False,
                split_algorithm=dc._RANDOM_SPLIT_ALGORITHM,
            )
            descriptor = load_family_dataset(descriptor_path)
        descriptors[family] = descriptor
    return descriptors


def _run_random_diagnostics(root, search_id, selected, descriptors):
    """Train one locked representative per task on random splits, without publishing."""
    import torch
    from types import SimpleNamespace
    from src.activity.model_card import load_prepared_training_data
    from src.activity.prepared_training import run_prepared_training

    runner = _runner_module()
    diagnostic_root = root / "outputs" / "activity_training" / search_id / "random_diagnostic"
    checkpoint_root = diagnostic_root / "checkpoints"
    models_dir = root / "data" / "activity" / "models" / search_id / "random-diagnostic"
    checkpoint_root.mkdir(parents=True, exist_ok=False)
    models_dir.mkdir(parents=True, exist_ok=False)
    results = {}
    with runner._process_overrides(models_dir):
        for key, selection in selected["selections"].items():
            family, task = key.split("/", 1)
            package_dir = root / "data" / "activity" / "prepared" / f"{family}-random-v2"
            manifest = package_dir / descriptors[family]["datasets"][task]["path"]
            options = {
                name: selection["options"][name]
                for name in BASE_OPTIONS
            }
            options["random_seed"] = selection["options"]["random_seed"]
            owner = SimpleNamespace(
                has_torch=True,
                device=torch.device("cuda"),
                job_id=f"{search_id}-random-{family}-{task}",
                status={},
                _update_progress=lambda *_args, **_kwargs: None,
            )
            checkpoint = checkpoint_root / f"{family}-{task}.pt"
            run_prepared_training(
                owner,
                manifest,
                **options,
                evaluate_test=True,
                publish=False,
                checkpoint_path=checkpoint,
            )
            if owner.status.get("state") != "completed":
                raise RuntimeError("Random diagnostic training did not complete")
            prepared = load_prepared_training_data(manifest)
            baseline = runner.baseline_metrics(
                task,
                prepared.frames["train"]["normalized_value"].tolist(),
                prepared.frames["test"]["normalized_value"].tolist(),
            )
            results[key] = {
                "family": family,
                "task": task,
                "package_id": descriptors[family]["package_id"],
                "split_algorithm": descriptors[family]["split"]["algorithm"],
                "random_seed": selection["options"]["random_seed"],
                "test_metrics": owner.status["test_metrics"],
                "test_baseline": baseline,
                "quality_assessment": runner.assess_quality(
                    task, owner.status["test_metrics"], baseline),
                "checkpoint_file": f"random_diagnostic/checkpoints/{checkpoint.name}",
                "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                "activated": False,
            }
    return results


def _deployment_gate(selected, comparison):
    """Report eligibility without activating any family bundle."""
    by_key = {(item["family"] + "/" + item["task"]): item
              for item in comparison
              if item.get("run_id") == "pde-buche-20261006-r2"}
    gates = {}
    for key, selection in selected["selections"].items():
        task = selection["task"]
        primary_keys = (
            ("roc_auc", "pr_auc", "balanced_accuracy")
            if task == "classification" else ("rmse", "mae", "r2")
        )
        baseline = selection.get("test_baseline")
        evals = selection.get("test_evaluations", [])
        baseline_ok = bool(evals) and all(
            all(
                (item["test_metrics"][metric] > baseline[metric])
                if task == "classification" or metric == "r2"
                else (item["test_metrics"][metric] < baseline[metric])
                for metric in primary_keys
            )
            for item in evals
        )
        current = by_key.get(key)
        current_ok = False
        if current and current.get("test_metrics"):
            current_metrics = current["test_metrics"]
            worst = selection["test_stability"]["metrics"]
            current_ok = all(
                (worst[metric]["worst"] >= current_metrics[metric])
                if task == "classification" or metric == "r2"
                else (worst[metric]["worst"] <= current_metrics[metric])
                for metric in primary_keys
            )
        reasons = []
        if not selection["stability"]["stable"]:
            reasons.append("validation_seed_instability")
        if not selection["test_stability"]["stable"]:
            reasons.append("scaffold_test_instability")
        if not baseline_ok:
            reasons.append("not_better_than_constant_baseline_for_all_test_seeds")
        if not current_ok:
            reasons.append("not_better_than_current_r2_candidate_or_comparison_missing")
        gates[key] = {
            "eligible": not reasons,
            "activated": False,
            "validation_stable": selection["stability"]["stable"],
            "scaffold_test_stable": selection["test_stability"]["stable"],
            "all_test_seeds_improve_constant_baseline": baseline_ok,
            "not_worse_than_current_r2": current_ok,
            "reasons": reasons,
        }
    return {
        "eligible": bool(gates) and all(item["eligible"] for item in gates.values()),
        "activated": False,
        "per_model": gates,
        "policy": "search never activates models; a separate reviewed activation is required",
    }


def run_search(search_id, *, root=ROOT, package_version="v2", existing_runs=(),
               candidate_runs=(), trials=_DEFAULT_TRIALS):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", search_id):
        raise ValueError("invalid search id")
    root = Path(root).resolve()
    search_dir = root / "outputs" / "activity_training" / search_id
    if search_dir.exists():
        raise FileExistsError("search id already exists")
    search_dir.mkdir(parents=True)
    runner = _runner_module()
    reports = [_load_report(run_id, root) for run_id in existing_runs]
    candidate_reports = [_load_report(run_id, root) for run_id in candidate_runs]
    candidates = ([{"run_id": report.get("run_id"), "source": "candidate", "report": report}
                   for report in candidate_reports]
                  + [{"run_id": report.get("run_id"), "source": "existing", "report": report}
                     for report in reports])
    original_options = dict(runner.OPTIONS)
    try:
        for trial in trials:
            trial_name = trial.get("name") if isinstance(trial, dict) else None
            trial = validate_trial(trial)
            if not isinstance(trial_name, str):
                raise ValueError("trial name is required")
            run_id = f"{search_id}-{trial_name}"
            runner.OPTIONS = dict(BASE_OPTIONS, **trial, evaluation_mode="validation_only")
            report = runner.run(run_id, root=root, package_version=package_version)
            candidates.append({"run_id": run_id, "source": "new", "report": report})
    finally:
        runner.OPTIONS = original_options
    # Existing runs are comparison evidence only.  They may have evaluated the
    # held-out set under an older protocol and must not participate in current
    # validation-only model selection.
    selected = select_models(_selection_reports(candidates))
    random_descriptors = _ensure_random_diagnostic_packages(root)
    selected = _finalize_selected_models(
        root, search_id, package_version, selected, _selection_reports(candidates))
    random_diagnostics = _run_random_diagnostics(
        root, search_id, selected, random_descriptors)
    comparison = []
    for report in reports:
        for job in report.get("jobs", []):
            if (isinstance(job, dict) and job.get("status") == "completed"
                    and job.get("family") in FAMILIES and job.get("task") in TASKS):
                comparison.append({
                    "run_id": report.get("run_id"),
                    "family": job["family"],
                    "task": job["task"],
                    "validation_metrics": job.get("validation_metrics"),
                    "test_metrics": job.get("test_metrics"),
                    "test_baseline": job.get("test_baseline"),
                    "model_id": job.get("model_id"),
                })
    deployment_gate = _deployment_gate(selected, comparison)
    output = {
        "search_id": search_id,
        "status": "completed",
        "package_version": package_version,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "activated": False,
        "selection_protocol": {
            "candidate_phase": "validation_only",
            "final_test_evaluation": "selected_hyperparameter_group_and_all_candidate_seeds_once",
            "test_selection_used": False,
        },
        "candidates": [{"run_id": item["run_id"], "source": item["source"],
                        "report_path": f"outputs/activity_training/{item['run_id']}/report.json"}
                       for item in candidates],
        **selected,
        "random_split_diagnostics": random_diagnostics,
        "comparison_to_existing_runs": comparison,
        "deployment_gate": deployment_gate,
        "limitations": [
            "Selection uses validation metrics only; held-out test metrics are evaluated once per selected checkpoint after selection.",
            "Metrics are model-development evidence, not clinical or experimental validation.",
            "Random-split interpolation diagnostics are not part of the scaffold deployment gate.",
        ],
    }
    (search_dir / "search_report.json").write_text(
        json.dumps(output, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-id", required=True)
    parser.add_argument("--package-version", default="v2")
    parser.add_argument("--existing-run", action="append", default=[])
    parser.add_argument("--candidate-run", action="append", default=[])
    parser.add_argument("--candidate-only", action="store_true")
    args = parser.parse_args(argv)
    result = run_search(args.search_id, package_version=args.package_version,
                        existing_runs=args.existing_run, candidate_runs=args.candidate_run,
                        trials=() if args.candidate_only else _DEFAULT_TRIALS)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
