import os
import time
import hashlib
import uuid
import threading
import logging
import random
from pathlib import Path
from typing import Dict, Any, List
import pandas as pd
import numpy as np

from src.activity.model_registry import ActivityModelRegistry

logger = logging.getLogger(__name__)

# Global dictionary to track training jobs
training_jobs: Dict[str, Dict[str, Any]] = {}
MODELS_DIR = Path("data/activity/models")
MODELS_DIR.mkdir(parents=True, exist_ok=True)


def get_model_registry() -> ActivityModelRegistry:
    return ActivityModelRegistry(MODELS_DIR)

def save_model_info(model_id: str, info: dict):
    if info.get("model_id") != model_id:
        raise ValueError("model_id does not match metadata")
    return get_model_registry().register(info)

def list_available_models():
    return get_model_registry().list()

def set_active_model(model_id: str):
    return get_model_registry().select(model_id)

def delete_model(model_id: str):
    """删除已注册的模型权重及其 metadata。"""
    return get_model_registry().delete(model_id)


def get_best_model():
    registry = get_model_registry()
    active = registry.get_active()
    if active is not None:
        return active

    models = registry.list()
    return models[0] if models else None

def get_best_model_path():
    model = get_best_model()
    if model is None:
        return None
    return str(get_model_registry().resolve_weights(model["model_id"]))


def get_current_model_id():
    model = get_best_model()
    return model["model_id"] if model is not None else None


def _sha256_file(file_path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(file_path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _split_indices(
    smiles_list: List[str],
    *,
    split_strategy: str = "scaffold",
    random_seed: int = 42,
    validation_fraction: float = 0.2,
) -> Dict[str, Any]:
    if isinstance(random_seed, bool) or not isinstance(random_seed, int):
        raise ValueError("random_seed must be an integer")
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be between 0 and 1")
    if len(smiles_list) < 2:
        raise ValueError("At least two samples are required for a train/validation split")

    requested_strategy = str(split_strategy).strip().lower()
    if requested_strategy not in {"scaffold", "random"}:
        raise ValueError("split_strategy must be 'scaffold' or 'random'")

    indices = list(range(len(smiles_list)))
    if requested_strategy == "random":
        from sklearn.model_selection import train_test_split

        train_indices, val_indices = train_test_split(
            indices,
            test_size=validation_fraction,
            random_state=random_seed,
        )
        return {
            "train_indices": list(train_indices),
            "val_indices": list(val_indices),
            "requested_strategy": requested_strategy,
            "actual_strategy": "random",
            "random_seed": random_seed,
            "warnings": [],
        }

    from rdkit import Chem
    from rdkit.Chem.Scaffolds import MurckoScaffold

    scaffold_groups: Dict[str, List[int]] = {}
    for index, smiles in enumerate(smiles_list):
        molecule = Chem.MolFromSmiles(str(smiles))
        if molecule is None:
            raise ValueError(
                f"Invalid SMILES at index {index}; scaffold split cannot be calculated"
            )
        scaffold = MurckoScaffold.MurckoScaffoldSmiles(
            mol=molecule,
            includeChirality=False,
        )
        scaffold_groups.setdefault(scaffold, []).append(index)

    if len(scaffold_groups) < 2:
        raise ValueError(
            "Scaffold split requires at least two distinct Bemis-Murcko scaffolds"
        )

    grouped_indices = list(scaffold_groups.values())
    random.Random(random_seed).shuffle(grouped_indices)
    grouped_indices.sort(key=len, reverse=True)
    target_val_size = max(
        1,
        min(len(smiles_list) - 1, round(len(smiles_list) * validation_fraction)),
    )

    val_groups: List[List[int]] = []
    val_size = 0
    for group in grouped_indices:
        proposed_size = val_size + len(group)
        if proposed_size >= len(smiles_list):
            continue
        if abs(proposed_size - target_val_size) < abs(val_size - target_val_size):
            val_groups.append(group)
            val_size = proposed_size

    if not val_groups:
        val_groups = [min(grouped_indices, key=len)]

    selected_group_ids = {id(group) for group in val_groups}
    val_indices = sorted(index for group in val_groups for index in group)
    train_indices = sorted(
        index
        for group in grouped_indices
        if id(group) not in selected_group_ids
        for index in group
    )
    if not train_indices or not val_indices:
        raise ValueError(
            "Scaffold split could not produce non-empty train and validation sets"
        )

    return {
        "train_indices": train_indices,
        "val_indices": val_indices,
        "requested_strategy": requested_strategy,
        "actual_strategy": "scaffold",
        "random_seed": random_seed,
        "warnings": [],
    }


def _build_model_metadata(
    *,
    model_id: str,
    weights_file: str,
    task_type: str,
    target_column: str,
    file_path: str,
    samples: int,
    best_metrics: dict,
    model_config: dict,
    endpoint: str | None = None,
    units: str = "unspecified",
    requested_split_strategy: str = "random",
    actual_split_strategy: str | None = None,
    random_seed: int = 42,
    split_warnings: List[str] | None = None,
    created_at: float | None = None,
) -> dict:
    created_at = time.time() if created_at is None else created_at
    metrics = dict(best_metrics or {})
    endpoint = target_column if endpoint is None else endpoint
    actual_split_strategy = actual_split_strategy or requested_split_strategy
    return {
        "model_id": model_id,
        "created_at": created_at,
        "task_type": task_type,
        "endpoint": endpoint,
        "units": units,
        "dataset_sha256": _sha256_file(file_path),
        "requested_split_strategy": requested_split_strategy,
        "split_strategy": actual_split_strategy,
        "random_seed": random_seed,
        "split_warnings": list(split_warnings or []),
        "model_config": dict(model_config),
        "model_format": "pytorch_state_dict",
        "weights_sha256": _sha256_file(MODELS_DIR / weights_file),
        "metrics": metrics,
        "target": target_column,
        "dataset": Path(file_path).name,
        "samples": samples,
        "best_metrics": metrics,
        "weights_file": weights_file,
        "name": f"{target_column} ({task_type}) - {time.strftime('%Y%m%d%H%M', time.localtime(created_at))}",
    }

class ActivityTrainer:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.status = training_jobs[job_id]
        
        try:
            import torch
            self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
            self.has_torch = True
            logger.info(f"Training initialized on device: {self.device}")
        except ImportError:
            self.device = None
            self.has_torch = False
            self._log("Error: PyTorch is required for training.")
            
    def _log(self, message: str):
        self.status["logs"].append(message)
        logger.info(f"[Job {self.job_id}] {message}")
        
    def _update_progress(
        self,
        progress: float,
        epoch: int,
        metrics: dict,
        train_loss: float = None,
        val_loss: float = None,
        learning_rate: float = None,
        best_epoch: int = None,
        best_metrics: dict = None,
    ):
        self.status["progress"] = progress
        self.status["epoch"] = epoch
        self.status["metrics"] = metrics
        if train_loss is not None:
            self.status["train_loss"] = float(train_loss)
        if val_loss is not None:
            self.status["val_loss"] = float(val_loss)
        if learning_rate is not None:
            self.status["learning_rate"] = float(learning_rate)
        if best_epoch is not None:
            self.status["best_epoch"] = int(best_epoch)
        if best_metrics is not None:
            self.status["best_metrics"] = best_metrics
        
    def start_training(self, 
                       file_path: str, 
                       target_column: str,
                       task_type: str = "regression",
                       epochs: int = 50,
                       lr: float = 0.001,
                       batch_size: int = 32,
                       dropout: float = 0.2,
                       num_layers: int = 5,
                       hidden_size: int = 256,
                       weight_decay: float = 1e-4,
                       patience: int = 12,
                       loss_metric: str = "MSE",
                       lr_scheduler: str = "Cosine",
                       split_strategy: str = "scaffold",
                       random_seed: int = 42):
        
        thread = threading.Thread(
            target=self._train_loop, 
            args=(file_path, target_column, task_type, epochs, lr, batch_size, dropout, 
                  num_layers, hidden_size, weight_decay, patience, loss_metric, lr_scheduler,
                  split_strategy, random_seed)
        )
        thread.start()
        
    def _train_loop(self, file_path, target_column, task_type, total_epochs, lr, batch_size, dropout,
                    num_layers, hidden_size, weight_decay, patience, loss_metric, lr_scheduler,
                    split_strategy, random_seed):
        start_time = time.time()
        self.status["state"] = "running"
        self._log(f"Starting training job {self.job_id}")
        self._log(f"Config: layers={num_layers}, hidden={hidden_size}, decay={weight_decay}, patience={patience}, loss={loss_metric}, scheduler={lr_scheduler}, split={split_strategy}, seed={random_seed}")
        
        if not self.has_torch:
            self.status["state"] = "failed"
            self.status["error"] = "PyTorch not installed"
            return
            
        import torch
        import torch.nn.functional as F
        from torch_geometric.data import Batch
        from src.activity.predictor import get_predictor
        from src.activity.rg_mpnn.Nets.ReduceGNN import RGNN
        
        try:
            # 1. Load Data
            self._log(f"Loading data from {file_path}...")
            df = None
            for enc in ['utf-8-sig', 'utf-8', 'gbk', 'gb18030', 'latin1']:
                try:
                    df = pd.read_csv(file_path, encoding=enc)
                    self._log(f"Successfully read CSV with {enc} encoding.")
                    break
                except Exception:
                    continue
            
            if df is None:
                raise ValueError("Could not decode CSV file. Please ensure it is saved as UTF-8/GBK.")
            
            # Strip whitespace from column names just in case
            df.columns = [str(col).strip() for col in df.columns]
            self._log(f"CSV Columns exactly found: {list(df.columns)}")
            
            # Find SMILES column
            smiles_col = None
            # 1. Try aliases
            for col in df.columns:
                if col.lower() in ['smiles', 'smile', 'canonical_smiles', 'canon_smiles', 'smiles_string', 'structure', 'mol', 'molecule']:
                    smiles_col = col
                    break
            
            # 2. Fallback: looking for columns with SMILES patterns if no alias matches
            if not smiles_col:
                for col in df.columns:
                    if df[col].dtype == object and len(df) > 0:
                        first_val = str(df[col].iloc[0])
                        # A very crude check: does it contains 'C' or 'c' and no spaces?
                        if ('c' in first_val.lower() or 'n' in first_val.lower()) and ' ' not in first_val:
                           smiles_col = col
                           break
            
            # 3. Final fallback: first object column
            if not smiles_col:
                for col in df.columns:
                    if df[col].dtype == object and len(df) > 0:
                        smiles_col = col
                        break
                        
            target_column = str(target_column).strip()
            if not smiles_col or target_column not in df.columns:
                raise ValueError(f"Could not find SMILES column or target '{target_column}'. Found columns: {list(df.columns)}")
            
            df = df.dropna(subset=[smiles_col, target_column])
            smiles_list = df[smiles_col].tolist()
            targets = df[target_column].tolist()
            
            # 2. Extract features
            self._log(f"Found {len(smiles_list)} valid molecules. Starting featurization...")
            predictor = get_predictor()
            
            valid_atom_data = []
            valid_rg_data = []
            valid_y = []
            valid_smiles = []
            
            for smi, y in zip(smiles_list, targets):
                processed = predictor.process_smiles(smi)
                if processed is not None:
                    atom_data, rg_data = processed
                    atom_data.y = torch.tensor([float(y)], dtype=torch.float)
                    rg_data.y = torch.tensor([float(y)], dtype=torch.float)
                    valid_atom_data.append(atom_data)
                    valid_rg_data.append(rg_data)
                    valid_y.append(y)
                    valid_smiles.append(str(smi))
                    
            if len(valid_atom_data) < 10:
                raise ValueError(f"Not enough valid molecules to train. Processed: {len(valid_atom_data)}")
                
            self._log(f"Successfully featurized {len(valid_atom_data)} molecules.")
            
            # 3. Task-configured split
            split_info = _split_indices(
                valid_smiles,
                split_strategy=split_strategy,
                random_seed=random_seed,
                validation_fraction=0.2,
            )
            train_idx = split_info["train_indices"]
            val_idx = split_info["val_indices"]
            self.status["requested_split_strategy"] = split_info[
                "requested_strategy"
            ]
            self.status["split_strategy"] = split_info["actual_strategy"]
            self.status["random_seed"] = split_info["random_seed"]
            self.status.setdefault("warnings", []).extend(split_info["warnings"])
            for warning in split_info["warnings"]:
                self._log(f"Split warning: {warning}")
            
            train_atom = [valid_atom_data[i] for i in train_idx]
            train_rg = [valid_rg_data[i] for i in train_idx]
            val_atom = [valid_atom_data[i] for i in val_idx]
            val_rg = [valid_rg_data[i] for i in val_idx]
            
            self._log(
                f"Split ({split_info['actual_strategy']}): "
                f"{len(train_idx)} train, {len(val_idx)} validation."
            )
            
            # 4. Model Setup (Dynamic dimension detection)
            atom_dim = valid_atom_data[0].x.shape[1]
            bond_dim = valid_atom_data[0].edge_attr.shape[1]
            self._log(f"Detected dimensions: atom_feat={atom_dim}, bond_feat={bond_dim}")
            
            x_dim = 256
            out_channels = 1 
            
            model = RGNN(in_channels=atom_dim,
                         channels=hidden_size,
                         out_channels=out_channels,
                         edge_dim=bond_dim,
                         num_passing_atom=num_layers,
                         num_passing_pool=1,
                         num_passing_rg=1,
                         num_passing_mol=1,
                         dropout=dropout).to(self.device)
            
            optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=float(weight_decay))
            
            # Select Scheduler
            if lr_scheduler == "Step":
                scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=20, gamma=0.5)
                scheduler_mode = "epoch"
            elif lr_scheduler == "Plateau":
                scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', factor=0.5, patience=patience)
                scheduler_mode = "plateau"
            else: # Cosine as default
                scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=total_epochs)
                scheduler_mode = "epoch"
            
            # Select Criterion
            if task_type == "regression":
                if loss_metric == "MAE": criterion = torch.nn.L1Loss()
                elif loss_metric == "Huber": criterion = torch.nn.HuberLoss()
                else: criterion = torch.nn.MSELoss()
            else:
                criterion = torch.nn.BCEWithLogitsLoss()
                
            # 5. Training Loop
            best_val_loss = float('inf')
            best_metrics = {}
            best_weights = None
            best_epoch = 0
            best_train_loss = None
            
            # Manual batching generator
            def get_batches(atom_list, rg_list, bs):
                for i in range(0, len(atom_list), bs):
                    a_batch = Batch.from_data_list(atom_list[i:i+bs]).to(self.device)
                    r_batch = Batch.from_data_list(rg_list[i:i+bs]).to(self.device)
                    yield a_batch, r_batch
            
            for epoch in range(1, total_epochs + 1):
                model.train()
                train_loss = 0
                batches = 0
                
                # Shuffle train
                perm = torch.randperm(len(train_atom))
                t_atom = [train_atom[i] for i in perm]
                t_rg = [train_rg[i] for i in perm]
                
                for a_batch, r_batch in get_batches(t_atom, t_rg, batch_size):
                    optimizer.zero_grad()
                    out, _ = model(a_batch, r_batch)
                    target = a_batch.y.view(-1)
                    
                    if task_type == "classification":
                        # Convert target to 0 or 1 roughly if not already
                        target = (target > 0.5).float()
                        
                    loss = criterion(out, target)
                    loss.backward()
                    optimizer.step()
                    
                    train_loss += loss.item() * a_batch.num_graphs
                    batches += a_batch.num_graphs
                    
                train_loss /= batches
                
                # Validation
                model.eval()
                val_loss = 0
                v_batches = 0
                all_preds = []
                all_targets = []
                
                with torch.no_grad():
                    for a_batch, r_batch in get_batches(val_atom, val_rg, batch_size):
                        out, _ = model(a_batch, r_batch)
                        target = a_batch.y.view(-1)
                        if task_type == "classification":
                            target = (target > 0.5).float()
                        loss = criterion(out, target)
                        val_loss += loss.item() * a_batch.num_graphs
                        v_batches += a_batch.num_graphs
                        
                        all_preds.extend(out.cpu().numpy())
                        all_targets.extend(target.cpu().numpy())
                        
                val_loss /= v_batches
                if scheduler_mode == "plateau":
                    scheduler.step(val_loss)
                else:
                    scheduler.step()
                
                # Calculate metrics
                from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error
                from sklearn.metrics import roc_auc_score, average_precision_score, accuracy_score
                
                preds = np.array(all_preds)
                targs = np.array(all_targets)
                metrics = {}
                
                if len(np.unique(targs)) > 1 or task_type == "regression": # Avoid error if all val targets are same
                    if task_type == "regression":
                        metrics["r2"] = float(r2_score(targs, preds))
                        metrics["mae"] = float(mean_absolute_error(targs, preds))
                        metrics["rmse"] = float(np.sqrt(mean_squared_error(targs, preds)))
                        main_metric = metrics["rmse"]
                        metric_log = f"R²={metrics['r2']:.3f}, MAE={metrics['mae']:.3f}, RMSE={metrics['rmse']:.3f}"
                    else:
                        probs = torch.sigmoid(torch.tensor(preds)).numpy()
                        preds_cls = (probs > 0.5).astype(int)
                        # Only calc AUC if >1 class in truth
                        if len(np.unique(targs)) > 1:
                            metrics["auc_roc"] = float(roc_auc_score(targs, probs))
                            metrics["auc_pr"] = float(average_precision_score(targs, probs))
                        else:
                            metrics["auc_roc"] = 0.0
                            metrics["auc_pr"] = 0.0
                        metrics["accuracy"] = float(accuracy_score(targs, preds_cls))
                        main_metric = -metrics["auc_pr"] # We want to minimize main_metric -> maximize AUC-PR
                        metric_log = f"ACC={metrics['accuracy']:.3f}, AUC-PR={metrics['auc_pr']:.3f}, AUC-ROC={metrics['auc_roc']:.3f}"
                else:
                    main_metric = val_loss
                    metric_log = "Loss only (single class in val)"

                current_lr = float(optimizer.param_groups[0]["lr"])
                metrics["train_loss"] = float(train_loss)
                metrics["val_loss"] = float(val_loss)
                metrics["learning_rate"] = current_lr
                    
                if (main_metric < best_val_loss) or (epoch == 1):
                    best_val_loss = main_metric
                    best_metrics = dict(metrics)
                    best_epoch = epoch
                    best_train_loss = float(train_loss)
                    import copy
                    best_weights = copy.deepcopy(model.state_dict())
                    
                if epoch % max(1, total_epochs // 20) == 0 or epoch == total_epochs:
                    self._log(f"Epoch {epoch}/{total_epochs} - Train Loss: {train_loss:.4f} - Val Loss: {val_loss:.4f} | {metric_log}")
                    
                self._update_progress(
                    (epoch / total_epochs) * 100,
                    epoch,
                    metrics,
                    train_loss=train_loss,
                    val_loss=val_loss,
                    learning_rate=current_lr,
                    best_epoch=best_epoch,
                    best_metrics=best_metrics,
                )
                
            # 6. Save Best Model
            if best_weights:
                model_filename = f"model_rgmpnn_{self.job_id}.pt"
                model_path = MODELS_DIR / model_filename
                torch.save({'state_dict': best_weights}, model_path)

                model_config = {
                    "in_channels": atom_dim,
                    "edge_dim": bond_dim,
                    "channels": hidden_size,
                    "out_channels": out_channels,
                    "num_passing_atom": num_layers,
                    "num_passing_pool": 1,
                    "num_passing_rg": 1,
                    "num_passing_mol": 1,
                    "dropout": dropout,
                }
                info = _build_model_metadata(
                    model_id=self.job_id,
                    weights_file=model_filename,
                    task_type=task_type,
                    target_column=target_column,
                    endpoint=target_column,
                    units=("probability" if task_type == "classification" else "unspecified"),
                    file_path=file_path,
                    samples=len(valid_atom_data),
                    best_metrics=best_metrics,
                    model_config=model_config,
                    requested_split_strategy=split_info["requested_strategy"],
                    actual_split_strategy=split_info["actual_strategy"],
                    random_seed=split_info["random_seed"],
                    split_warnings=split_info["warnings"],
                )
                try:
                    save_model_info(self.job_id, info)
                except Exception:
                    model_path.unlink(missing_ok=True)
                    raise
                
                self._log(f"Training completed successfully! Model saved as {model_filename}")
                self.status["state"] = "completed"
                self.status["metrics"] = best_metrics
                self.status["best_metrics"] = best_metrics
                self.status["best_epoch"] = best_epoch
                self.status["train_loss"] = best_train_loss
                self.status["val_loss"] = best_metrics.get("val_loss")
            else:
                self.status["state"] = "failed"
                self.status["error"] = "Training failed to produce weights."
                
        except Exception as e:
            self.status["state"] = "failed"
            self.status["error"] = str(e)
            self._log(f"Training failed with error: {str(e)}")
            import traceback
            self._log(traceback.format_exc())
            
        finally:
            elapsed = time.time() - start_time
            self.status["elapsed"] = elapsed
            if os.path.exists(file_path):
                try:
                    os.remove(file_path) # Clean up temp dataset
                except: pass

def submit_training_job(**kwargs):
    job_id = str(uuid.uuid4())[:8]
    training_jobs[job_id] = {
        "state": "pending",
        "progress": 0,
        "epoch": 0,
        "metrics": {},
        "best_metrics": {},
        "best_epoch": 0,
        "train_loss": None,
        "val_loss": None,
        "learning_rate": None,
        "logs": [],
        "warnings": [],
        "elapsed": 0
    }
    trainer = ActivityTrainer(job_id)
    trainer.start_training(**kwargs)
    return job_id

def get_job_status(job_id: str):
    return training_jobs.get(job_id, None)
