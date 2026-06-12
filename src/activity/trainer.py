import os
import time
import json
import uuid
import threading
import logging
from pathlib import Path
from typing import Dict, Any, List
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# Global dictionary to track training jobs
training_jobs: Dict[str, Dict[str, Any]] = {}
MODELS_DIR = Path("data/activity/models")
MODELS_DIR.mkdir(parents=True, exist_ok=True)
ACTIVE_MODEL_OVERRIDE = None

def save_model_info(model_id: str, info: dict):
    info_path = MODELS_DIR / f"{model_id}_info.json"
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

def list_available_models():
    models = []
    if not MODELS_DIR.exists():
        return models
    for fp in MODELS_DIR.glob("*_info.json"):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                info = json.load(f)
            models.append(info)
        except Exception as e:
            logger.warning(f"Failed to read model info {fp}: {e}")
    # Sort by creation time descending
    models.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return models

def set_active_model(model_filename: str):
    global ACTIVE_MODEL_OVERRIDE
    ACTIVE_MODEL_OVERRIDE = model_filename

def delete_model(model_id: str):
    """删除模型权重及其描述信息"""
    models = list_available_models()
    target_info = next((m for m in models if m['model_id'] == model_id), None)
    if not target_info:
        raise ValueError("模型不存在")
    
    # 获取文件名
    weights_file = target_info.get("weights_file")
    info_file = f"{model_id}_info.json"
    
    # 物理删除
    if weights_file:
        weights_path = MODELS_DIR / weights_file
        if weights_path.exists():
            os.remove(weights_path)
            
    info_path = MODELS_DIR / info_file
    if info_path.exists():
        os.remove(info_path)
    
    return True

def get_best_model_path():
    global ACTIVE_MODEL_OVERRIDE
    if ACTIVE_MODEL_OVERRIDE:
        return str(MODELS_DIR / ACTIVE_MODEL_OVERRIDE)
    
    models = list_available_models()
    if not models:
        return None
    # Pick the most recent one as default "current" model for now
    return str(MODELS_DIR / models[0]["weights_file"])

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
                       lr_scheduler: str = "Cosine"):
        
        thread = threading.Thread(
            target=self._train_loop, 
            args=(file_path, target_column, task_type, epochs, lr, batch_size, dropout, 
                  num_layers, hidden_size, weight_decay, patience, loss_metric, lr_scheduler)
        )
        thread.start()
        
    def _train_loop(self, file_path, target_column, task_type, total_epochs, lr, batch_size, dropout,
                    num_layers, hidden_size, weight_decay, patience, loss_metric, lr_scheduler):
        start_time = time.time()
        self.status["state"] = "running"
        self._log(f"Starting training job {self.job_id}")
        self._log(f"Config: layers={num_layers}, hidden={hidden_size}, decay={weight_decay}, patience={patience}, loss={loss_metric}, scheduler={lr_scheduler}")
        
        if not self.has_torch:
            self.status["state"] = "failed"
            self.status["error"] = "PyTorch not installed"
            return
            
        import torch
        import torch.nn.functional as F
        from torch_geometric.data import Batch
        from sklearn.model_selection import train_test_split
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
            
            for smi, y in zip(smiles_list, targets):
                processed = predictor.process_smiles(smi)
                if processed is not None:
                    atom_data, rg_data = processed
                    atom_data.y = torch.tensor([float(y)], dtype=torch.float)
                    rg_data.y = torch.tensor([float(y)], dtype=torch.float)
                    valid_atom_data.append(atom_data)
                    valid_rg_data.append(rg_data)
                    valid_y.append(y)
                    
            if len(valid_atom_data) < 10:
                raise ValueError(f"Not enough valid molecules to train. Processed: {len(valid_atom_data)}")
                
            self._log(f"Successfully featurized {len(valid_atom_data)} molecules.")
            
            # 3. Random Split
            indices = list(range(len(valid_atom_data)))
            train_idx, val_idx = train_test_split(indices, test_size=0.2, random_state=42)
            
            train_atom = [valid_atom_data[i] for i in train_idx]
            train_rg = [valid_rg_data[i] for i in train_idx]
            val_atom = [valid_atom_data[i] for i in val_idx]
            val_rg = [valid_rg_data[i] for i in val_idx]
            
            self._log(f"Split: {len(train_idx)} train, {len(val_idx)} validation.")
            
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
                
                # Save info
                info = {
                    "model_id": self.job_id,
                    "created_at": time.time(),
                    "task_type": task_type,
                    "target": target_column,
                    "dataset": Path(file_path).name,
                    "samples": len(valid_atom_data),
                    "best_metrics": best_metrics,
                    "weights_file": model_filename,
                    "model_config": {
                        "in_channels": atom_dim,
                        "edge_dim": bond_dim,
                        "channels": hidden_size,
                        "out_channels": out_channels,
                        "num_passing_atom": num_layers,
                        "num_passing_pool": 1,
                        "num_passing_rg": 1,
                        "num_passing_mol": 1,
                        "dropout": dropout,
                    },
                    "name": f"{target_column} ({task_type}) - {time.strftime('%Y%m%d%H%M')}"
                }
                save_model_info(self.job_id, info)
                
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
        "elapsed": 0
    }
    trainer = ActivityTrainer(job_id)
    trainer.start_training(**kwargs)
    return job_id

def get_job_status(job_id: str):
    return training_jobs.get(job_id, None)
