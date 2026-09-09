"""Training lifecycle for hash-verified, three-way scaffold datasets.

No endpoint-ready registration is possible before held-out evaluation. This
module deliberately does not inherit the legacy CSV label coercion/cleanup.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

import numpy as np

_TRAINING_RNG_LOCK = threading.Lock()


def training_code_provenance():
    """Record code identity without including source text, credentials or host paths."""
    root = Path(__file__).resolve().parents[2]
    activity = root / "src" / "activity"
    paths = [activity / name for name in ("trainer.py", "prepared_training.py", "model_card.py",
                                         "dataset_contract.py", "model_registry.py", "predictor.py")]
    paths.extend((activity / "rg_mpnn").rglob("*.py"))
    files = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(paths)}
    result = {"files": files, "source_sha256": hashlib.sha256(
        json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest(), "git_commit": "unknown"}
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                  text=True, timeout=3, check=True).stdout.strip()
        if re.fullmatch(r"[0-9a-f]{40,64}", revision):
            result["git_commit"] = revision
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def calculate_metrics(task_type, labels, predictions):
    from sklearn.metrics import (average_precision_score, balanced_accuracy_score,
                                 confusion_matrix, mean_absolute_error,
                                 mean_squared_error, r2_score, roc_auc_score)
    from scipy.special import expit

    y, p = np.asarray(labels, dtype=float), np.asarray(predictions, dtype=float)
    if y.ndim != 1 or p.shape != y.shape or len(y) < 2:
        raise ValueError("Evaluation requires at least two aligned scalar observations")
    if not np.isfinite(y).all() or not np.isfinite(p).all():
        raise ValueError("Non-finite evaluation values")
    if task_type == "regression":
        result = {"rmse": float(np.sqrt(mean_squared_error(y, p))),
                  "mae": float(mean_absolute_error(y, p)), "r2": float(r2_score(y, p))}
    elif task_type == "classification":
        if set(y) != {0., 1.}:
            raise ValueError("Classification evaluation requires both binary classes")
        probabilities = expit(p)
        predicted = (probabilities >= 0.5).astype(int)
        tn, fp, fn, tp = confusion_matrix(y, predicted, labels=[0, 1]).ravel()
        result = {"roc_auc": float(roc_auc_score(y, probabilities)),
                  "pr_auc": float(average_precision_score(y, probabilities)),
                  "balanced_accuracy": float(balanced_accuracy_score(y, predicted)),
                  "confusion_matrix": dict(zip(("tn", "fp", "fn", "tp"),
                                               map(int, (tn, fp, fn, tp))))}
    else:
        raise ValueError("Unsupported task_type")
    json.dumps(result, allow_nan=False)
    return result


def train_epoch(model, loader, criterion, optimizer, device):
    model.train()
    total, count = 0., 0
    for atoms, reduced in loader:
        atoms, reduced = atoms.to(device), reduced.to(device)
        optimizer.zero_grad()
        output, _ = model(atoms, reduced)
        loss = criterion(output.reshape(-1), atoms.y.reshape(-1))
        if not math.isfinite(float(loss.detach())):
            raise ValueError("Non-finite training loss")
        loss.backward()
        optimizer.step()
        total += float(loss.detach()) * atoms.num_graphs
        count += atoms.num_graphs
    if not count:
        raise ValueError("Empty training split")
    return total / count


def evaluate(model, loader, criterion, task_type, device, *, prediction_summary=None):
    import torch
    model.eval()
    total, count, labels, predictions = 0., 0, [], []
    with torch.no_grad():
        for atoms, reduced in loader:
            atoms, reduced = atoms.to(device), reduced.to(device)
            output, _ = model(atoms, reduced)
            output, y = output.reshape(-1), atoms.y.reshape(-1)
            total += float(criterion(output, y)) * atoms.num_graphs
            count += atoms.num_graphs
            predictions.extend(output.cpu().tolist())
            labels.extend(y.cpu().tolist())
    if not count or not math.isfinite(total):
        raise ValueError("Empty or non-finite evaluation split")
    metrics = calculate_metrics(task_type, labels, predictions)
    if prediction_summary is not None:
        from scipy.special import expit
        values = expit(predictions) if task_type == "classification" else np.asarray(predictions)
        prediction_summary.update(sample_count=count,
                                  output_kind="probability" if task_type == "classification" else "endpoint_value",
                                  minimum=float(np.min(values)), maximum=float(np.max(values)),
                                  mean=float(np.mean(values)))
    return total / count, metrics


def fit_prepared(model, optimizer, loaders, criterion, *, task_type, device,
                 epochs, patience, scheduler=None, plateau=False, progress=None):
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in (epochs, patience)):
        raise ValueError("epochs and patience must be positive integers")
    best_score, best_weights, best_epoch = math.inf, None, 0
    best_metrics = None
    for epoch in range(1, epochs + 1):
        train_loss = train_epoch(model, loaders["train"], criterion, optimizer, device)
        val_loss, metrics = evaluate(model, loaders["validation"], criterion, task_type, device)
        score = metrics["rmse"] if task_type == "regression" else -metrics["pr_auc"]
        if not all(math.isfinite(value) for value in (score, val_loss, train_loss)):
            raise ValueError("Non-finite checkpoint selection metric")
        if score < best_score:
            best_score, best_epoch = score, epoch
            best_weights = copy.deepcopy(model.state_dict())
            best_metrics = {**metrics, "val_loss": val_loss, "train_loss": train_loss}
        if scheduler is not None:
            scheduler.step(val_loss) if plateau else scheduler.step()
        if progress is not None:
            progress(epoch, {**metrics, "train_loss": train_loss, "val_loss": val_loss}, best_epoch)
        if epoch - best_epoch >= patience:
            break
    model.load_state_dict(best_weights)
    # The held-out loader is never visited during optimization or checkpoint selection.
    summary = {}
    _, test_metrics = evaluate(model, loaders["test"], criterion, task_type, device,
                               prediction_summary=summary)
    return {"best_epoch": best_epoch, "epochs_completed": epoch,
            "validation_metrics": best_metrics, "test_metrics": test_metrics,
            "test_prediction_summary": summary}


class _PairedBatches:
    def __init__(self, pairs, batch_size, *, shuffle=False, seed=42):
        import torch
        self.pairs, self.batch_size, self.shuffle = pairs, batch_size, shuffle
        self.generator = torch.Generator().manual_seed(seed)

    def __iter__(self):
        import torch
        from torch_geometric.data import Batch
        order = torch.randperm(len(self.pairs), generator=self.generator).tolist() if self.shuffle else list(range(len(self.pairs)))
        for offset in range(0, len(order), self.batch_size):
            pairs = [self.pairs[i] for i in order[offset:offset + self.batch_size]]
            yield (Batch.from_data_list([p[0] for p in pairs]),
                   Batch.from_data_list([p[1] for p in pairs]))


def _claim_feature_identity(atoms, split, owners):
    """Keep held-out inputs disjoint after the predictor's actual preprocessing."""
    from rdkit import Chem
    from src.activity.dataset_contract import _scaffold_identity

    smiles = getattr(atoms, "feature_smiles", None)
    mol = Chem.MolFromSmiles(smiles) if isinstance(smiles, str) and smiles else None
    if mol is None:
        raise ValueError("Featurized molecule identity is unavailable")
    # Current RGNN features have no stereochemical identity channel. Do not
    # treat stereo-only differences as independent held-out scaffolds.
    Chem.RemoveStereochemistry(mol)
    identity = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=False)
    scaffold = _scaffold_identity(mol)
    for kind, value in (("molecule", identity), ("scaffold", scaffold)):
        if not value:
            continue
        key = (kind, value)
        previous = owners.setdefault(key, split)
        if previous != split:
            raise ValueError(f"Featurized {kind} overlap between {previous} and {split}; refusing training")


def run_prepared_training(owner, manifest_path, **options):
    if not owner.has_torch:
        raise ValueError("PyTorch not installed")
    import torch
    # Serialize this lifecycle's process-global RNG use and restore the caller's
    # state. Other unrelated Torch users/processes are outside this guarantee.
    with _TRAINING_RNG_LOCK:
        devices = list(range(torch.cuda.device_count())) if torch.cuda.is_available() else []
        with torch.random.fork_rng(devices=devices):
            return _run_prepared_training(owner, manifest_path, **options)


def _run_prepared_training(owner, manifest_path, *, epochs, lr, batch_size, dropout,
                          num_layers, hidden_size, weight_decay, patience,
                          loss_metric, lr_scheduler, random_seed):
    from src.activity.model_card import load_prepared_training_data
    prepared = load_prepared_training_data(manifest_path)
    if not owner.has_torch:
        raise ValueError("PyTorch not installed")
    for name, value in {"epochs": epochs, "batch_size": batch_size, "num_layers": num_layers,
                        "hidden_size": hidden_size, "patience": patience}.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if num_layers < 2:
        raise ValueError("num_layers must be at least 2 for the existing RGNN network")
    if isinstance(random_seed, bool) or not isinstance(random_seed, int) or not 0 <= random_seed < 2**32:
        raise ValueError("random_seed must be a uint32 integer")
    if not all(math.isfinite(float(v)) for v in (lr, dropout, weight_decay)) or lr <= 0 or not 0 <= dropout < 1 or weight_decay < 0:
        raise ValueError("Invalid optimizer hyperparameters")
    import torch
    from src.activity.predictor import get_predictor
    from src.activity.rg_mpnn.Nets.ReduceGNN import RGNN
    manifest = prepared.manifest
    code_provenance = training_code_provenance()
    torch.manual_seed(random_seed)
    task_type = manifest["task_type"]
    owner.status.update(task_type=task_type, split_strategy="scaffold", random_seed=random_seed,
                        dataset_split_seed=manifest["split"]["seed"])
    # Only graph featurization is reused; no active/demo predictor output is used.
    predictor = get_predictor()
    loaders = {}
    feature_owners = {}
    first = None
    for split, frame in prepared.frames.items():
        pairs = []
        for smi, value in zip(frame["canonical_smiles"], frame["normalized_value"]):
            processed = predictor.process_smiles(smi)
            if processed is None:
                raise ValueError(f"Featurization failed in {split}; no rows may be silently dropped")
            atoms, reduced = processed
            _claim_feature_identity(atoms, split, feature_owners)
            atoms.y = torch.tensor([float(value)], dtype=torch.float32)
            reduced.y = atoms.y.clone()
            pairs.append((atoms, reduced))
            if first is None:
                first = atoms
        loaders[split] = _PairedBatches(pairs, batch_size, shuffle=split == "train", seed=random_seed)
    config = dict(in_channels=first.x.shape[1], edge_dim=first.edge_attr.shape[1],
                  channels=hidden_size, out_channels=1, num_passing_atom=num_layers,
                  num_passing_pool=1, num_passing_rg=1, num_passing_mol=1, dropout=dropout)
    model = RGNN(**config).to(owner.device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    if lr_scheduler == "Plateau":
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=patience)
    elif lr_scheduler == "Step":
        scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=20, gamma=0.5)
    elif lr_scheduler == "Cosine":
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    else:
        raise ValueError("Unsupported lr_scheduler")
    criteria = {"MSE": torch.nn.MSELoss, "MAE": torch.nn.L1Loss, "Huber": torch.nn.HuberLoss}
    if loss_metric not in criteria:
        raise ValueError("Unsupported loss_metric")
    criterion = criteria[loss_metric]() if task_type == "regression" else torch.nn.BCEWithLogitsLoss()
    def progress(epoch, metrics, best_epoch):
        owner._update_progress(95 * epoch / epochs, epoch, metrics, best_epoch=best_epoch,
                               train_loss=metrics["train_loss"], val_loss=metrics["val_loss"])
    result = fit_prepared(model, optimizer, loaders, criterion, task_type=task_type,
                          device=owner.device, epochs=epochs, patience=patience,
                          scheduler=scheduler, plateau=lr_scheduler == "Plateau", progress=progress)
    if training_code_provenance()["source_sha256"] != code_provenance["source_sha256"]:
        raise ValueError("Training source files changed during the job; refusing registration")
    metadata = publish_model(owner.job_id, model, prepared, config, result, random_seed,
                             code_provenance=code_provenance)
    owner.status.update(state="completed", progress=100, metrics=result["validation_metrics"],
                        best_metrics=result["validation_metrics"], test_metrics=result["test_metrics"],
                        best_epoch=result["best_epoch"], epochs_completed=result["epochs_completed"],
                        model_id=metadata["model_id"], scientific_readiness="endpoint_ready")


def publish_model(job_id, model, prepared, config, result, random_seed, *, code_provenance=None):
    import torch
    from src.activity.model_card import build_model_card, write_model_card
    from src.activity.trainer import get_activity_models_dir, save_model_info
    from src.activity.model_registry import validate_endpoint_metadata
    manifest = prepared.manifest
    # Unique names never overwrite existing registered assets, even on retries.
    model_id = str(uuid.uuid4())
    directory = get_activity_models_dir()
    weights = directory / f"model_rgmpnn_{model_id}.pt"
    card_path = directory / f"model_rgmpnn_{model_id}.card.json"
    owned_weights = owned_card = False
    try:
        with weights.open("xb") as handle:
            owned_weights = True
            torch.save({"state_dict": model.state_dict()}, handle)
        metadata = dict(model_id=model_id, created_at=time.time(), task_type=manifest["task_type"],
                        target_id=manifest["target_id"], target_name=manifest["target_name"],
                        endpoint=manifest["output_endpoint"], units=manifest["output_units"],
                        endpoint_key=manifest["endpoint_key"], label_transform=manifest["label_transform"],
                        dataset_sha256=prepared.manifest_sha256,
                        prepared_dataset_sha256=manifest["prepared_dataset_sha256"],
                        weights_file=weights.name, weights_sha256=hashlib.sha256(weights.read_bytes()).hexdigest(),
                        model_config=config, model_format="pytorch_state_dict", split_strategy="scaffold",
                        requested_split_strategy="scaffold", random_seed=random_seed,
                        split_counts=manifest["split"]["counts"],
                        split_scaffold_counts=manifest["split"]["scaffold_counts"],
                        metrics=result["validation_metrics"], best_metrics=result["validation_metrics"],
                        test_metrics=result["test_metrics"], scientific_readiness="endpoint_ready",
                        demo_mode=False, fallback_used=False, samples=sum(manifest["split"]["counts"].values()),
                        best_epoch=result["best_epoch"], epochs_completed=result["epochs_completed"],
                        source=manifest["source"], license=manifest["license"],
                        source_sha256=manifest["input_sha256"], model_contract_key=manifest["model_contract_key"],
                        dataset_split_seed=manifest["split"]["seed"],
                        data_quality_summary=prepared.quality_report,
                        test_prediction_summary=result.get("test_prediction_summary", {}),
                        training_code=code_provenance or training_code_provenance())
        card = build_model_card(metadata=metadata, validation_metrics=result["validation_metrics"],
                                test_metrics=result["test_metrics"], limitations=[
                                    "Performance is limited to the supplied dataset and scaffold split, not experimental validation.",
                                    "A matching content hash proves integrity, not experimental authenticity.",
                                    "Bitwise reproducibility across concurrent jobs or GPU platforms is not guaranteed."])
        digest = write_model_card(card_path, card)
        owned_card = True
        metadata.update(model_card_file=card_path.name, model_card_sha256=digest)
        metadata = validate_endpoint_metadata(metadata)
        save_model_info(model_id, metadata)
        return metadata
    except Exception:
        if owned_weights:
            weights.unlink(missing_ok=True)
        if owned_card:
            card_path.unlink(missing_ok=True)
        raise
