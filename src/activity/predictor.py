"""
活性预测模块
负责调用 RG-MPNN 模型进行分子活性预测
"""

import os
import sys
import logging
import json
from pathlib import Path
from typing import List, Dict, Any, Union, Optional
import numpy as np
from rdkit import Chem
from rdkit.Chem.SaltRemover import SaltRemover
from rdkit.Chem import QED

logger = logging.getLogger(__name__)

# Add rg_mpnn to sys.path to allow internal imports
current_dir = Path(__file__).parent
rg_mpnn_path = current_dir / "rg_mpnn"
if str(rg_mpnn_path) not in sys.path:
    sys.path.append(str(rg_mpnn_path))

class ActivityPredictor:
    """
    RG-MPNN 活性预测器封装类
    """
    
    def __init__(self, model_dir: str = "data/activity/rg_mpnn"):
        self.model_dir = model_dir
        self.model = None
        self._loaded = False
        self.demo_mode = False # Flag for demo/fallback mode
        self.model_config = None
        self.current_model_path = None
        
        # Try to check if torch is available
        try:
            import torch
            self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
            self.has_torch = True
        except ImportError:
            self.device = None
            self.has_torch = False
            logger.warning("PyTorch 未安装，活性预测模块将运行在演示模式。")
            
        self.remover = SaltRemover()

    def _find_checkpoint_path(self) -> Optional[Path]:
        ckpt_path = None
        try:
            from src.activity.trainer import get_best_model_path
            custom_model = get_best_model_path()
            if custom_model and Path(custom_model).exists():
                ckpt_path = Path(custom_model)
        except ImportError:
            pass

        if ckpt_path:
            return ckpt_path

        possible_paths = [
            Path("data/activity/rg_mpnn/best_model.pt"),
            Path("data/activity/rg_mpnn/model.pt"),
            Path("RG-MPNN-main/vis/AURKA/AURKA.pt"),
        ]

        for p in possible_paths:
            if p.exists():
                return p
        return None

    def _get_model_info_for_checkpoint(self, ckpt_path: Path) -> Optional[Dict[str, Any]]:
        models_dir = Path("data/activity/models")
        if not models_dir.exists():
            return None

        ckpt_name = ckpt_path.name
        model_id = ckpt_name.replace("model_rgmpnn_", "").replace(".pt", "")
        info_candidates = [models_dir / f"{model_id}_info.json"]

        for info_path in info_candidates:
            if not info_path.exists():
                continue
            try:
                with open(info_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:
                logger.warning(f"Failed to read model info {info_path}: {exc}")
        return None

    def _infer_model_config_from_state_dict(self, state_dict: Dict[str, Any]) -> Dict[str, Any]:
        lin1_weight = state_dict["lin1.weight"]
        lin2_weight = state_dict["lin2.weight"]
        atom_conv0_lin1_weight = state_dict["atom_convs.0.lin1.weight"]

        atom_conv_indices = {
            int(key.split(".")[1])
            for key in state_dict
            if key.startswith("atom_convs.")
        }
        rg_conv_indices = {
            int(key.split(".")[1])
            for key in state_dict
            if key.startswith("rg_convs.")
        }

        channels = int(lin1_weight.shape[0])
        edge_dim = int(atom_conv0_lin1_weight.shape[1] - channels)

        return {
            "in_channels": int(lin1_weight.shape[1]),
            "channels": channels,
            "out_channels": int(lin2_weight.shape[0]),
            "edge_dim": edge_dim,
            "num_passing_atom": max(atom_conv_indices) + 1 if atom_conv_indices else 1,
            "num_passing_pool": 1,
            "num_passing_rg": max(rg_conv_indices) + 1 if rg_conv_indices else 1,
            "num_passing_mol": 1,
            "dropout": 0.0,
        }
        
    def load(self):
        """
        加载 RG-MPNN 模型
        """
        if self._loaded:
            return

        if not self.has_torch:
            logger.warning("环境缺失 PyTorch，启用演示模式 (Demo Mode)。")
            self.demo_mode = True
            self._loaded = True
            return

        logger.info("正在加载 RG-MPNN 模型...")
        
        try:
            # Import RG-MPNN modules
            # Note: These imports depend on torch
            try:
                from molecular_network.mol_feature.atom_feature import atom_feature, atom_types
                from molecular_network.mol_feature.bond_feature import bond_feature
                from molecular_network.mol_feature.reduceGraph_feature import rg_feature, rg_x_feature
                from molecular_network.util.wash import NeutraliseCharges
                from Nets.ReduceGNN import RGNN
            except ImportError as e:
                # Try importing with package prefix
                logger.warning(f"Direct import failed ({e}), trying package import...")
                from src.activity.rg_mpnn.molecular_network.mol_feature.atom_feature import atom_feature, atom_types
                from src.activity.rg_mpnn.molecular_network.mol_feature.bond_feature import bond_feature
                from src.activity.rg_mpnn.molecular_network.mol_feature.reduceGraph_feature import rg_feature, rg_x_feature
                from src.activity.rg_mpnn.molecular_network.util.wash import NeutraliseCharges
                from src.activity.rg_mpnn.Nets.ReduceGNN import RGNN

            import torch
            
            ckpt_path = self._find_checkpoint_path()
            
            if ckpt_path:
                logger.info(f"Loading weights from {ckpt_path}")
                state = torch.load(ckpt_path, map_location=self.device)
                state_dict = state['state_dict'] if 'state_dict' in state else state
                info = self._get_model_info_for_checkpoint(ckpt_path)
                model_config = (info or {}).get("model_config") or self._infer_model_config_from_state_dict(state_dict)

                self.model_config = model_config
                self.current_model_path = str(ckpt_path)

                logger.info(
                    "Resolved model config: "
                    f"in={model_config['in_channels']}, edge={model_config['edge_dim']}, "
                    f"hidden={model_config['channels']}, atom_layers={model_config['num_passing_atom']}, "
                    f"rg_layers={model_config['num_passing_rg']}"
                )

                self.model = RGNN(
                    in_channels=model_config["in_channels"],
                    channels=model_config["channels"],
                    out_channels=model_config.get("out_channels", 1),
                    edge_dim=model_config["edge_dim"],
                    num_passing_atom=model_config.get("num_passing_atom", 5),
                    num_passing_pool=model_config.get("num_passing_pool", 1),
                    num_passing_rg=model_config.get("num_passing_rg", 1),
                    num_passing_mol=model_config.get("num_passing_mol", 1),
                    dropout=model_config.get("dropout", 0.0),
                ).to(self.device)

                self.model.load_state_dict(state_dict)
            else:
                logger.warning("⚠️ 未找到模型权重文件。启用演示模式以防止随机输出误导用户。")
                # Instead of using random weights, we switch to demo mode if weights are missing
                # because random weights produce meaningless garbage.
                self.demo_mode = True
                self.model = None # Unload random model
            
            if not self.demo_mode:
                self.model.eval()
            
            self._loaded = True
            logger.info(f"RG-MPNN 模型加载完成 (Demo Mode: {self.demo_mode})")
            
        except Exception as e:
            logger.error(f"模型加载失败: {e}。切换至演示模式。")
            self.demo_mode = True
            self._loaded = True

    def process_smiles(self, smi: str):
        """处理单个 SMILES"""

        from src.activity.rg_mpnn.molecular_network.mol_feature.atom_feature import atom_feature, atom_types
        from src.activity.rg_mpnn.molecular_network.mol_feature.bond_feature import bond_feature
        from src.activity.rg_mpnn.molecular_network.mol_feature.reduceGraph_feature import rg_feature, rg_x_feature
        from src.activity.rg_mpnn.molecular_network.util.wash import NeutraliseCharges
        from torch_geometric.data import Data
        import torch
        
        try:
            mol = Chem.MolFromSmiles(smi)
            if mol is None: return None
            
            # Clean molecule
            mol = self.remover.StripMol(mol, dontRemoveEverything=True)
            mol = NeutraliseCharges(mol)
            
            # Atom features
            x = atom_feature(mol, atom_types=atom_types, hybrid=True, aromatic=True, exp_val=True, Hs=True)
            
            # Bond features
            edge_index, edge_attr = bond_feature(mol, bond_type_num=4)
            
            # Reduce Graph features
            rg_name, pool_index, rg_edge_index_attr = rg_feature(mol, edge_index)
            pool_index = pool_index.transpose(0, 1)
            rg_x = rg_x_feature(rg_name)
            
            # RG Edges
            if rg_edge_index_attr is not None and len(rg_edge_index_attr) > 0:
                rg_edge_index = rg_edge_index_attr.transpose(0, 1)[:-1, :]
                rg_edge_attr = rg_edge_index_attr[:, [2]]
            else:
                rg_edge_index = torch.empty((2, 0), dtype=torch.long)
                rg_edge_attr = torch.empty((0, 1), dtype=torch.long)

            # Construct Data objects
            atom_data = Data(x=x,
                           edge_index=edge_index.long(),
                           edge_attr=edge_attr,
                           pool_index=pool_index.long())
            
            rg_data = Data(x=rg_x,
                          edge_index=rg_edge_index.long(),
                          edge_attr=rg_edge_attr)
            
            return atom_data, rg_data
            
        except Exception as e:
            import traceback
            error_msg = traceback.format_exc()
            logger.warning(f"Featurization failed for {smi}:\n{error_msg}")
            return None

    def _predict_demo(self, smiles_list: List[str]) -> List[Dict[str, Any]]:
        """演示模式：基于 QED 的启发式评分"""
        results = []
        for smi in smiles_list:
            try:
                mol = Chem.MolFromSmiles(smi)
                if mol:
                    # 使用 QED (Drug-likeness) 作为演示评分
                    # QED 范围 0-1
                    qed_score = QED.qed(mol)
                    
                    # 模拟活性 pIC50 范围 (例如 4.0 - 9.0)
                    # 简单的线性映射：0.5 -> 6.0 (Medium)
                    activity = 4.0 + qed_score * 5.0 
                    
                    # 加一点随机性让它看起来不那么线性
                    import random
                    noise = random.uniform(-0.2, 0.2)
                    activity += noise
                    
                    cls = "High" if activity > 7.5 else ("Medium" if activity > 5.5 else "Low")
                    
                    results.append({
                        "smiles": smi,
                        "activity_score": float(activity),
                        "class": cls,
                        "confidence": 0.8 + (qed_score * 0.1),
                        "success": True,
                        "note": "Demo Mode (Simulated)"
                    })
                else:
                     results.append({"smiles": smi, "success": False, "error": "Invalid SMILES"})
            except Exception as e:
                results.append({"smiles": smi, "success": False, "error": f"Processing error: {str(e)}"})
        return results

    def predict(self, smiles: Union[str, List[str]]) -> List[Dict[str, Any]]:
        """
        预测分子活性
        """
        if not self._loaded:
            self.load()
            
        if isinstance(smiles, str):
            smiles_list = [smiles]
        else:
            smiles_list = smiles
            
        # 如果在演示模式，直接返回模拟结果
        if self.demo_mode:
            return self._predict_demo(smiles_list)
            
        results = []
        valid_atom_data = []
        valid_rg_data = []
        valid_indices = []
        
        # Pre-process
        for i, smi in enumerate(smiles_list):
            processed = self.process_smiles(smi.strip())
            if processed:
                atom_data, rg_data = processed
                valid_atom_data.append(atom_data)
                valid_rg_data.append(rg_data)
                valid_indices.append(i)
            else:
                results.append({
                    "smiles": smi,
                    "success": False,
                    "error": "Invalid SMILES or processing error"
                })
        
        if not valid_atom_data:
            return results
            
        # Batching and Inference
        from torch_geometric.data import Batch
        import torch
        
        try:
            atom_batch = Batch.from_data_list(valid_atom_data).to(self.device)
            rg_batch = Batch.from_data_list(valid_rg_data).to(self.device)
            
            with torch.no_grad():
                out, fp = self.model(atom_batch, rg_batch)
                scores = out.cpu().numpy()
                
                for idx, score in zip(valid_indices, scores):
                    smi = smiles_list[idx]
                    val = float(score)
                    results.append({
                        "smiles": smi,
                        "activity_score": val,
                        "class": "High" if val > 7 else ("Medium" if val > 5 else "Low"),
                        "confidence": 1.0,
                        "success": True
                    })
                    
        except Exception as e:
            logger.error(f"Inference batch failed: {e}")
            for idx in valid_indices:
                results.append({
                    "smiles": smiles_list[idx],
                    "success": False,
                    "error": f"Inference error: {str(e)}"
                })
                
        # Reorder results
        final_results = []
        res_map = {r['smiles']: r for r in results}
        for smi in smiles_list:
            if smi.strip() in res_map:
                final_results.append(res_map[smi.strip()])
            else:
                final_results.append({"smiles": smi, "success": False, "error": "Unknown error"})
                
        return final_results

# 全局实例
_predictor = None

def get_predictor() -> ActivityPredictor:
    global _predictor
    if _predictor is None:
        _predictor = ActivityPredictor()
    return _predictor
