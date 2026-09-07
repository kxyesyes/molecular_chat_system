"""One frozen, serial baseline run. Never selects or activates trained models."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OPTIONS = dict(epochs=50, patience=12, batch_size=32, lr=.001, num_layers=5,
               hidden_size=256, dropout=.2, weight_decay=.0001,
               lr_scheduler="Cosine", loss_metric="MSE", random_seed=42)
FAMILIES = ("pde-family", "buche-family")
TASKS = ("classification", "regression")


def aggregate_status(states):
    if len(states) == 4 and all(s == "completed" for s in states):
        return "completed"
    return "partial" if "completed" in states else "failed"


def baseline_metrics(task, train_labels, test_labels):
    import numpy as np
    from src.activity.prepared_training import calculate_metrics
    train = np.asarray(train_labels, dtype=float)
    if not len(train) or not np.isfinite(train).all():
        raise ValueError("Invalid baseline training labels")
    constant = float(train.mean())
    if task == "classification":
        if set(train) != {0., 1.}:
            raise ValueError("Classifier training labels must contain both classes")
        constant = float(np.log(constant / (1. - constant)))
    return calculate_metrics(task, test_labels, [constant] * len(test_labels))


def assess_quality(task, metrics, baseline):
    """Descriptive comparison only, never a checkpoint/deployment selection rule."""
    keys = ("roc_auc", "pr_auc", "balanced_accuracy") if task == "classification" else ("rmse", "mae", "r2")
    if any(k not in metrics or k not in baseline for k in keys):
        return {"status": "unavailable"}
    comparisons = {k: metrics[k] < baseline[k] if k in ("rmse", "mae")
                   else metrics[k] > baseline[k] for k in keys}
    return {"status": "improves_all_metrics" if all(comparisons.values()) else
            "mixed" if any(comparisons.values()) else "does_not_improve_baseline",
            "strict_improvements": comparisons, "clinical_validation": False}


def write_report(path, data):
    """Replace only this run's report, atomically; no private molecule rows."""
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(encoded)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def train_job(manifest, job_id, progress):
    import torch
    from src.activity.prepared_training import run_prepared_training
    owner = SimpleNamespace(has_torch=True, device=torch.device("cuda"),
                            job_id=job_id, status={})
    owner._update_progress = progress
    run_prepared_training(owner, manifest, **OPTIONS)
    return owner.status


def run(run_id, *, root=ROOT):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", run_id):
        raise ValueError("Invalid run ID")
    root = Path(root).resolve()
    output = root / "outputs/activity_training" / run_id
    models = root / "data/activity/models" / run_id
    if output.exists() or models.exists():
        raise FileExistsError("Run ID already exists; refusing automatic repeat")
    output.mkdir(parents=True, exist_ok=False)
    models.mkdir(parents=True, exist_ok=False)
    report_path = output / "report.json"
    report = dict(run_id=run_id, status="preflight", options=dict(OPTIONS), jobs=[], bundles=[], bundle_errors=[],
                  activated=False, started_at=datetime.now(timezone.utc).isoformat(),
                  joint_test_consistency=None,
                  limitations=["One frozen scaffold baseline, not clinical validation.",
                    "Joint held-out consistency unavailable: individual predictions are not retained; test is not rerun."])
    write_report(report_path, report)
    old_directory = os.environ.get("ACTIVITY_MODEL_DIR")
    old_logging = logging.root.manager.disable
    rdkit_logs = None
    os.environ["ACTIVITY_MODEL_DIR"] = str(models)
    started = time.monotonic()
    try:
        import torch
        from rdkit import rdBase
        from src.activity.family_dataset import load_family_dataset
        from src.activity.model_card import load_prepared_training_data
        from src.activity.model_registry import ActivityModelRegistry
        if not torch.cuda.is_available():
            raise RuntimeError("Approved GPU baseline requires CUDA")
        report["device"] = dict(name=torch.cuda.get_device_name(0), torch=torch.__version__)
        # Do not emit molecule strings from third-party featurization diagnostics.
        logging.disable(logging.CRITICAL)
        rdkit_logs = rdBase.BlockLogs()
        packages = {}
        for family in FAMILIES:
            path = root / "data/activity/prepared" / (family + "-v1") / "family_dataset.json"
            descriptor = load_family_dataset(path)
            packages[family] = (path, descriptor)
            print(json.dumps(dict(event="package_verified", family=family)), flush=True)
        registry = ActivityModelRegistry(models)
        for family, (path, descriptor) in packages.items():
            family_jobs = {}
            for task in TASKS:
                job = dict(family=family, task=task, status="running", phase="training")
                report["jobs"].append(job)
                report["status"] = "running"
                write_report(report_path, report)
                job_started = time.monotonic()
                def progress(_progress, epoch, metrics, **kwargs):
                    job.update(epoch=epoch, validation_metrics=metrics,
                               best_epoch=kwargs.get("best_epoch"))
                    write_report(report_path, report)
                    print(json.dumps(dict(event="epoch", family=family, task=task,
                                          epoch=epoch, best_epoch=job["best_epoch"], metrics=metrics)), flush=True)
                try:
                    manifest = path.parent / descriptor["datasets"][task]["path"]
                    status = train_job(manifest, f"{run_id}-{family}-{task}", progress)
                    if status.get("state") != "completed" or not status.get("model_id"):
                        raise RuntimeError("Trainer returned no completed registered model")
                    job.update(status="completed", phase="baseline", model_id=status["model_id"],
                               best_epoch=status["best_epoch"], epochs_completed=status["epochs_completed"],
                               validation_metrics=status["best_metrics"], test_metrics=status["test_metrics"])
                    # Constants fitted solely from train labels; no trained test re-inference.
                    prepared = load_prepared_training_data(manifest)
                    job["test_baseline"] = baseline_metrics(task,
                        prepared.frames["train"]["normalized_value"].tolist(),
                        prepared.frames["test"]["normalized_value"].tolist())
                    job["quality_assessment"] = assess_quality(task, job["test_metrics"], job["test_baseline"])
                    job["phase"] = "complete"
                    family_jobs[task] = job["model_id"]
                except Exception as exc:
                    job.update(status="failed", error_type=type(exc).__name__)
                finally:
                    job["elapsed_seconds"] = round(time.monotonic() - job_started, 3)
                    write_report(report_path, report)
                    print(json.dumps(dict(event="job_finished", **job), ensure_ascii=False), flush=True)
                    torch.cuda.empty_cache()
            if len(family_jobs) == 2:
                try:
                    bundle = registry.register_family_bundle(bundle_id=f"{run_id}-{family}",
                        family_dataset_path=path,
                        classification_model_id=family_jobs["classification"],
                        regression_model_id=family_jobs["regression"])
                    report["bundles"].append(bundle["bundle_id"])
                except Exception as exc:
                    report["bundle_errors"].append(dict(family=family, error_type=type(exc).__name__))
                write_report(report_path, report)
        report["status"] = aggregate_status([job["status"] for job in report["jobs"]])
        if report["bundle_errors"] and report["status"] == "completed":
            report["status"] = "partial"
    except Exception as exc:
        report.update(status="partial" if any(j["status"] == "completed" for j in report["jobs"]) else "failed",
                      error_type=type(exc).__name__)
    finally:
        rdkit_logs = None
        logging.disable(old_logging)
        if old_directory is None:
            os.environ.pop("ACTIVITY_MODEL_DIR", None)
        else:
            os.environ["ACTIVITY_MODEL_DIR"] = old_directory
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write_report(report_path, report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    result = run(args.run_id)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    raise SystemExit(0 if result["status"] == "completed" else 1)
