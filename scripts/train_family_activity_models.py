"""One frozen CUDA baseline: four serial jobs, registration without activation.

CLI only. Concurrent calls through this module are rejected because environment,
logging and diagnostic streams are process-global. This is not a Web scheduler;
unrelated callers or separately imported copies are outside that guarantee.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
OPTIONS = dict(epochs=50, patience=12, batch_size=32, lr=.001, num_layers=5,
               hidden_size=256, dropout=.2, weight_decay=.0001,
               lr_scheduler="Cosine", loss_metric="MSE", random_seed=42)
FAMILIES = ("pde-family", "buche-family")
TASKS = ("classification", "regression")
_RUN_LOCK = threading.Lock()
_METRICS = ("roc_auc", "pr_auc", "balanced_accuracy", "rmse", "mae", "r2",
            "val_loss", "train_loss")


def aggregate_status(states):
    if len(states) == 4 and all(s == "completed" for s in states):
        return "completed"
    return "partial" if "completed" in states else "failed"


def baseline_metrics(task, train_labels, test_labels):
    """Fit only a training-set constant; never select using held-out values."""
    import numpy as np
    from src.activity.prepared_training import calculate_metrics
    train = np.asarray(train_labels, dtype=float)
    if task not in TASKS or train.ndim != 1 or not len(train) or not np.isfinite(train).all():
        raise ValueError("Invalid baseline training labels")
    constant = float(train.mean())
    if task == "classification":
        if set(train) != {0., 1.}:
            raise ValueError("Classifier training labels must contain both classes")
        constant = float(np.log(constant / (1. - constant)))
    return calculate_metrics(task, test_labels, [constant] * len(test_labels))


def assess_quality(task, metrics, baseline):
    """Descriptive comparison only, never checkpoint/deployment selection."""
    keys = (("roc_auc", "pr_auc", "balanced_accuracy") if task == "classification"
            else ("rmse", "mae", "r2"))
    if any(k not in metrics or k not in baseline for k in keys):
        return {"status": "unavailable"}
    comparisons = {k: metrics[k] < baseline[k] if k in ("rmse", "mae")
                   else metrics[k] > baseline[k] for k in keys}
    return {"status": "improves_all_metrics" if all(comparisons.values()) else
            "mixed" if any(comparisons.values()) else "does_not_improve_baseline",
            "strict_improvements": comparisons, "clinical_validation": False}


def _safe_metrics(metrics):
    """Allow aggregate scalars only; never forward arbitrary trainer payloads."""
    if not isinstance(metrics, dict):
        raise ValueError("Invalid aggregate metrics")
    result = {}
    for key in _METRICS:
        if key in metrics:
            value = metrics[key]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("Invalid aggregate metric")
            result[key] = value
    if "confusion_matrix" in metrics:
        counts = metrics["confusion_matrix"]
        if (not isinstance(counts, dict) or set(counts) != {"tn", "fp", "fn", "tp"}
                or any(type(v) is not int or v < 0 for v in counts.values())):
            raise ValueError("Invalid confusion matrix")
        result["confusion_matrix"] = dict(counts)
    return result


def _epoch(value):
    if type(value) is not int or not 1 <= value <= OPTIONS["epochs"]:
        raise ValueError("Invalid epoch")
    return value


def _error_type(exc):
    # Custom exception class names, as well as messages, can contain input data.
    allowed = (ValueError, TypeError, RuntimeError, FileNotFoundError, FileExistsError,
               PermissionError, OSError, ImportError, ModuleNotFoundError,
               KeyboardInterrupt, SystemExit)
    return type(exc).__name__ if type(exc) in allowed else "Exception"


def write_report(path, data):
    """Atomic replacement, including cleanup if encoding/writing/replace fails."""
    path = Path(path)
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def train_job(manifest, job_id, progress):
    import torch
    from src.activity.prepared_training import run_prepared_training
    owner = SimpleNamespace(has_torch=True, device=torch.device("cuda"),
                            job_id=job_id, status={}, _update_progress=progress)
    run_prepared_training(owner, manifest, **OPTIONS)
    return owner.status


@contextlib.contextmanager
def _process_overrides(models):
    old_directory = os.environ.get("ACTIVITY_MODEL_DIR")
    old_logging = logging.root.manager.disable
    try:
        os.environ["ACTIVITY_MODEL_DIR"] = str(models)
        logging.disable(logging.CRITICAL)
        # Discard, rather than retain, third-party diagnostics. Structured events
        # use the caller's saved stream, so epoch updates remain visible.
        with open(os.devnull, "w", encoding="utf-8") as sink:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                from rdkit import rdBase
                with rdBase.BlockLogs():
                    yield
    finally:
        logging.disable(old_logging)
        if old_directory is None:
            os.environ.pop("ACTIVITY_MODEL_DIR", None)
        else:
            os.environ["ACTIVITY_MODEL_DIR"] = old_directory


def _failed_status(report):
    return "partial" if any(j["status"] == "completed" for j in report["jobs"]) else "failed"


def _execute(root, models, run_id, report, report_path, events):
    import torch
    from src.activity.family_dataset import load_family_dataset
    from src.activity.model_card import load_prepared_training_data
    from src.activity.model_registry import ActivityModelRegistry
    if not torch.cuda.is_available():
        raise RuntimeError("Approved GPU baseline requires CUDA")
    # Resolve the prepared lifecycle's delayed imports before ANY job starts.
    # Import symbols, not just package specs: installed but broken dependencies
    # must fail preflight too. No predictor/model/optimizer is instantiated.
    from src.activity.rg_mpnn.Nets.ReduceGNN import RGNN
    from src.activity.prepared_training import run_prepared_training, calculate_metrics
    from src.activity.predictor import get_predictor
    # ActivityPredictor.process_smiles and _PairedBatches.__iter__.
    from src.activity.rg_mpnn.molecular_network.mol_feature.atom_feature import atom_feature, atom_types
    from src.activity.rg_mpnn.molecular_network.mol_feature.bond_feature import bond_feature
    from src.activity.rg_mpnn.molecular_network.mol_feature.reduceGraph_feature import rg_feature, rg_x_feature
    from src.activity.rg_mpnn.molecular_network.util.wash import NeutraliseCharges
    from torch_geometric.data import Data, Batch
    # Frozen optimizer/scheduler/losses and calculate_metrics/evaluate.
    from torch.optim import Adam
    from torch.optim.lr_scheduler import CosineAnnealingLR
    from torch.nn import MSELoss, BCEWithLogitsLoss
    from sklearn.metrics import (average_precision_score, balanced_accuracy_score,
                                 confusion_matrix, mean_absolute_error,
                                 mean_squared_error, r2_score, roc_auc_score)
    from scipy.special import expit
    # publish_model imports these after fitting and held-out evaluation.
    from src.activity.trainer import get_activity_models_dir, save_model_info
    from src.activity.model_card import build_model_card, write_model_card
    from src.activity.model_registry import validate_endpoint_metadata
    report["device"] = dict(name=torch.cuda.get_device_name(0), torch=torch.__version__)

    def emit(event, **fields):
        print(json.dumps(dict(event=event, **fields), ensure_ascii=False, allow_nan=False),
              file=events, flush=True)

    packages = {}
    for family in FAMILIES:
        path = root / "data/activity/prepared" / (family + "-v1") / "family_dataset.json"
        descriptor = load_family_dataset(path)
        # The core loader verifies/recomputes its descriptor, but cannot know
        # which loop family the CLI intended to train.
        if descriptor["family_id"] != family:
            raise ValueError("Prepared package does not match expected family")
        packages[family] = (path, descriptor)
        emit("package_verified", family=family)

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
                job.update(epoch=_epoch(epoch), validation_metrics=_safe_metrics(metrics),
                           best_epoch=_epoch(kwargs.get("best_epoch")))
                write_report(report_path, report)
                emit("epoch", family=family, task=task, epoch=job["epoch"],
                     best_epoch=job["best_epoch"], metrics=job["validation_metrics"])

            try:
                manifest = path.parent / descriptor["datasets"][task]["path"]
                status = train_job(manifest, f"{run_id}-{family}-{task}", progress)
                model_id = status.get("model_id")
                if (status.get("state") != "completed" or not isinstance(model_id, str)
                        or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}", model_id)):
                    raise RuntimeError("Trainer returned no completed registered model")
                job.update(phase="baseline", model_id=model_id,
                           best_epoch=_epoch(status["best_epoch"]),
                           epochs_completed=_epoch(status["epochs_completed"]),
                           validation_metrics=_safe_metrics(status["best_metrics"]),
                           test_metrics=_safe_metrics(status["test_metrics"]))
                # Reuse main's verified loader. No trained test re-inference;
                # constants are fitted solely from train labels.
                prepared = load_prepared_training_data(manifest)
                job["test_baseline"] = baseline_metrics(task,
                    prepared.frames["train"]["normalized_value"].tolist(),
                    prepared.frames["test"]["normalized_value"].tolist())
                job["quality_assessment"] = assess_quality(task, job["test_metrics"], job["test_baseline"])
                job.update(status="completed", phase="complete")
                family_jobs[task] = model_id
            except (KeyboardInterrupt, SystemExit) as exc:
                job.update(status="failed", error_type=_error_type(exc))
                raise
            except Exception as exc:
                job.update(status="failed", error_type=_error_type(exc))
            finally:
                job["elapsed_seconds"] = round(time.monotonic() - job_started, 3)
                write_report(report_path, report)
                emit("job_finished", **job)
                torch.cuda.empty_cache()

        if len(family_jobs) == 2:
            try:
                # Main's registry revalidates weights/cards, shared data/splits,
                # thresholds and model-group identity. Never trust status alone.
                bundle_id = f"{run_id}-{family}"
                bundle = registry.register_family_bundle(bundle_id=bundle_id,
                    family_dataset_path=path,
                    classification_model_id=family_jobs["classification"],
                    regression_model_id=family_jobs["regression"])
                if bundle["bundle_id"] != bundle_id:
                    raise ValueError("Registered bundle identity mismatch")
                report["bundles"].append(bundle_id)
            except Exception as exc:
                report["bundle_errors"].append(dict(family=family, error_type=_error_type(exc)))
            write_report(report_path, report)
    report["status"] = aggregate_status([job["status"] for job in report["jobs"]])
    if report["status"] == "completed" and len(report["bundles"]) != len(FAMILIES):
        report["status"] = "partial"


def run(run_id, *, root=ROOT):
    """Reserve a fresh run; reject same-module concurrency before any overrides."""
    if not _RUN_LOCK.acquire(blocking=False):
        raise RuntimeError("Family training runner already running")
    try:
        return _run(run_id, root=Path(root).resolve())
    except FileExistsError:
        raise FileExistsError("Run ID already exists; refusing automatic repeat") from None
    except OSError:
        # Filesystem errors may contain private absolute paths. Preserve the
        # public reused-ID contract without exposing the underlying exception.
        raise OSError("Unable to persist training run") from None
    finally:
        _RUN_LOCK.release()


def _run(run_id, *, root):
    if (not isinstance(run_id, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", run_id)
            or Path(run_id).is_reserved()):
        raise ValueError("Invalid run ID")
    output = root / "outputs/activity_training" / run_id
    models = root / "data/activity/models" / run_id
    if os.path.lexists(output) or os.path.lexists(models):
        raise FileExistsError("Run ID already exists; refusing automatic repeat")
    output.mkdir(parents=True, exist_ok=False)
    models.mkdir(parents=True, exist_ok=False)
    report_path = output / "report.json"
    report = dict(run_id=run_id, status="preflight", options=dict(OPTIONS), jobs=[], bundles=[], bundle_errors=[],
                  activated=False, started_at=datetime.now(timezone.utc).isoformat(),
                  joint_test_consistency=None,
                  limitations=["One frozen scaffold baseline, not clinical validation.",
                    "Joint held-out consistency unavailable: individual predictions are not retained; test is not rerun."])
    started = time.monotonic()
    events = sys.stdout
    try:
        write_report(report_path, report)
        with _process_overrides(models):
            _execute(root, models, run_id, report, report_path, events)
    except (KeyboardInterrupt, SystemExit) as exc:
        report.update(status=_failed_status(report), error_type=_error_type(exc))
        raise
    except Exception as exc:
        report.update(status=_failed_status(report), error_type=_error_type(exc))
    finally:
        for job in report["jobs"]:
            if job["status"] == "running":
                job.update(status="failed", error_type=report.get("error_type", "Exception"))
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        try:
            write_report(report_path, report)
        except Exception:
            # One best-effort failure snapshot, never a training retry. If the
            # filesystem remains unavailable the caller receives a safe error.
            report.update(status=_failed_status(report), error_type="OSError")
            try:
                write_report(report_path, report)
            except Exception:
                pass
            raise OSError("Unable to persist training report") from None
    return report


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("Invalid command arguments")


def main(argv=None):
    try:
        parser = _Parser(description=__doc__)
        parser.add_argument("--run-id", required=True)
        args = parser.parse_args(argv)
        # Direct script execution needs the repository on sys.path. Importing
        # this module has no path, model, environment or logging side effects.
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        try:
            result = run(args.run_id)
        except (KeyboardInterrupt, SystemExit) as exc:
            # run() has persisted failure and restored process globals. Never
            # forward training's exit code/message to the interpreter. Keep
            # argparse's normal --help SystemExit(0) outside this boundary.
            result = dict(status="failed", activated=False, error_type=_error_type(exc))
    except Exception as exc:
        result = dict(status="failed", activated=False, error_type=_error_type(exc))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
