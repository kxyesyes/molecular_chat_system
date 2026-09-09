"""
活性预测模块
负责调用 RG-MPNN 模型进行分子活性预测
"""

import sys
import logging
import inspect
import math
from pathlib import Path
from typing import List, Dict, Any, Union, Optional, Tuple
from rdkit import Chem
from rdkit.Chem.SaltRemover import SaltRemover

logger = logging.getLogger(__name__)

_SUPPORTED_TASK_TYPES = {"regression", "classification"}
_INVALID_ENDPOINTS = {"unknown", "unspecified"}


def _validate_prediction_metadata(metadata: Any) -> Dict[str, Any]:
    if not isinstance(metadata, dict):
        raise ValueError("Activity model metadata is unavailable")

    task_type = metadata.get("task_type")
    if task_type not in _SUPPORTED_TASK_TYPES:
        raise ValueError("Activity model metadata has an invalid task_type")

    endpoint = metadata.get("endpoint")
    if (
        not isinstance(endpoint, str)
        or not endpoint.strip()
        or endpoint.strip().lower() in _INVALID_ENDPOINTS
    ):
        raise ValueError("Activity model metadata has an invalid endpoint")

    units = metadata.get("units")
    if not isinstance(units, str) or not units.strip():
        raise ValueError("Activity model metadata has invalid or missing units")

    model_id = metadata.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("Activity model metadata has an invalid model_id")

    weights_sha256 = metadata.get("weights_sha256")
    if (
        not isinstance(weights_sha256, str)
        or len(weights_sha256) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in weights_sha256)
    ):
        raise ValueError("Activity model metadata has an invalid weights_sha256")

    return dict(metadata)


def _safe_torch_load(torch_module, checkpoint_path: Path, device):
    load_kwargs = {"map_location": device}
    try:
        if "weights_only" in inspect.signature(torch_module.load).parameters:
            load_kwargs["weights_only"] = True
    except (TypeError, ValueError):
        logger.warning("Unable to inspect torch.load signature; using compatible arguments")
    return torch_module.load(checkpoint_path, **load_kwargs)

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
        self.current_model_metadata = None
        self.model_unavailable_reason = None
        
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

    def _find_checkpoint(self) -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
        try:
            from src.activity.trainer import get_best_model, get_model_registry

            metadata = get_best_model()
            if metadata is not None:
                checkpoint = get_model_registry().resolve_weights(metadata["model_id"])
                return checkpoint, metadata
        except (ImportError, ValueError) as exc:
            logger.warning("Registered activity model is unavailable: %s", exc)

        possible_paths = [
            Path("data/activity/rg_mpnn/best_model.pt"),
            Path("data/activity/rg_mpnn/model.pt"),
            Path("RG-MPNN-main/vis/AURKA/AURKA.pt"),
        ]

        for p in possible_paths:
            if p.exists():
                # A historical filename does not imply global activation of a
                # registered prepared endpoint model.
                metadata = self._get_model_info_for_checkpoint(p)
                if (metadata is not None
                        and metadata.get("scientific_readiness", "legacy_unvalidated") != "legacy_unvalidated"):
                    continue
                return p, None
        return None, None

    def _find_checkpoint_path(self) -> Optional[Path]:
        checkpoint, _metadata = self._find_checkpoint()
        return checkpoint

    def _get_model_info_for_checkpoint(self, ckpt_path: Path) -> Optional[Dict[str, Any]]:
        try:
            from src.activity.trainer import get_model_registry

            registry = get_model_registry()
            resolved_checkpoint = ckpt_path.resolve(strict=True)
            for metadata in registry.list():
                if registry.resolve_weights(metadata["model_id"]) == resolved_checkpoint:
                    return metadata
        except (ImportError, OSError, ValueError) as exc:
            logger.warning("Unable to resolve registered checkpoint metadata: %s", exc)
        return None

    def invalidate(self) -> None:
        self.model = None
        self._loaded = False
        self.demo_mode = False
        self.model_config = None
        self.current_model_path = None
        self.current_model_metadata = None
        self.model_unavailable_reason = None

    def _mark_unavailable(self, reason: str) -> None:
        self.demo_mode = True
        self.model = None
        self.model_config = None
        self.current_model_path = None
        self.current_model_metadata = None
        self.model_unavailable_reason = reason
        self._loaded = True

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

        self.demo_mode = False
        self.model = None
        self.model_config = None
        self.current_model_path = None
        self.current_model_metadata = None
        self.model_unavailable_reason = None

        if not self.has_torch:
            self._mark_unavailable(
                "PyTorch is unavailable; no RG-MPNN prediction was calculated."
            )
            return

        ckpt_path, info = self._find_checkpoint()
        if ckpt_path is None:
            self._mark_unavailable(
                "RG-MPNN model weights are unavailable; no prediction was calculated."
            )
            return
        try:
            validated_metadata = _validate_prediction_metadata(info)
        except ValueError as exc:
            logger.warning("Activity model metadata is unavailable or invalid: %s", exc)
            self._mark_unavailable(str(exc))
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
            
            if ckpt_path:
                logger.info(f"Loading weights from {ckpt_path}")
                state = _safe_torch_load(torch, ckpt_path, self.device)
                state_dict = state['state_dict'] if 'state_dict' in state else state
                model_config = validated_metadata.get(
                    "model_config"
                ) or self._infer_model_config_from_state_dict(state_dict)

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
                self.current_model_metadata = validated_metadata
            
            self._loaded = True
            logger.info(f"RG-MPNN 模型加载完成 (Demo Mode: {self.demo_mode})")
            
        except Exception as e:
            logger.error(f"模型加载失败: {e}。切换至演示模式。")
            self._mark_unavailable(
                f"RG-MPNN model load failed; no prediction was calculated: {e}"
            )

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
                           pool_index=pool_index.long(),
                           feature_smiles=Chem.MolToSmiles(mol, canonical=True))
            
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
        """Return an explicit failure when no validated RG-MPNN model is loaded."""
        reason = self.model_unavailable_reason or (
            "RG-MPNN model weights are unavailable; no prediction was calculated."
        )
        return [
            {
                "smiles": smi,
                "success": False,
                "error": reason,
                "note": "Model unavailable",
            }
            for smi in smiles_list
        ]

    def _predict_task_aware(
        self, smiles: Union[str, List[str]]
    ) -> List[Dict[str, Any]]:
        if not self._loaded:
            self.load()

        smiles_list = [smiles] if isinstance(smiles, str) else list(smiles)
        if self.demo_mode:
            return self._predict_demo(smiles_list)

        try:
            metadata = _validate_prediction_metadata(self.current_model_metadata)
        except ValueError as exc:
            return [
                {"smiles": smi, "success": False, "error": str(exc)}
                for smi in smiles_list
            ]

        if self.model is None:
            return [
                {
                    "smiles": smi,
                    "success": False,
                    "error": (
                        "RG-MPNN model is unavailable; "
                        "no prediction was calculated."
                    ),
                }
                for smi in smiles_list
            ]

        results: List[Optional[Dict[str, Any]]] = [None] * len(smiles_list)
        valid_atom_data = []
        valid_rg_data = []
        valid_indices = []

        for index, smi in enumerate(smiles_list):
            if not isinstance(smi, str) or not smi.strip():
                results[index] = {
                    "smiles": smi,
                    "success": False,
                    "error": "Invalid SMILES or processing error",
                }
                continue
            processed = self.process_smiles(smi.strip())
            if processed is None:
                results[index] = {
                    "smiles": smi,
                    "success": False,
                    "error": "Invalid SMILES or processing error",
                }
                continue
            atom_data, rg_data = processed
            valid_atom_data.append(atom_data)
            valid_rg_data.append(rg_data)
            valid_indices.append(index)

        if valid_atom_data:
            from torch_geometric.data import Batch
            import torch

            try:
                atom_batch = Batch.from_data_list(valid_atom_data).to(self.device)
                rg_batch = Batch.from_data_list(valid_rg_data).to(self.device)
                with torch.no_grad():
                    output, _fingerprint = self.model(atom_batch, rg_batch)
                    if metadata["task_type"] == "classification":
                        predictions = (
                            torch.sigmoid(output)
                            .detach()
                            .cpu()
                            .reshape(-1)
                            .tolist()
                        )
                        output_key = "probability"
                    else:
                        predictions = output.detach().cpu().reshape(-1).tolist()
                        output_key = "value"

                if len(predictions) != len(valid_indices):
                    raise ValueError(
                        "Model output count does not match the input batch"
                    )

                for index, raw_value in zip(valid_indices, predictions):
                    value = float(raw_value)
                    if not math.isfinite(value):
                        raise ValueError("Model returned a non-finite prediction")
                    results[index] = {
                        "smiles": smiles_list[index],
                        "success": True,
                        "task_type": metadata["task_type"],
                        "endpoint": metadata["endpoint"],
                        output_key: value,
                        "units": metadata["units"],
                    }
            except Exception as exc:
                logger.error("Inference batch failed: %s", exc)
                for index in valid_indices:
                    results[index] = {
                        "smiles": smiles_list[index],
                        "success": False,
                        "error": f"Inference error: {exc}",
                    }

        return [
            result
            if result is not None
            else {
                "smiles": smiles_list[index],
                "success": False,
                "error": "Unknown error",
            }
            for index, result in enumerate(results)
        ]

    def predict(self, smiles: Union[str, List[str]]) -> List[Dict[str, Any]]:
        """Predict registered endpoint values without inferring task semantics."""
        return self._predict_task_aware(smiles)

# 全局实例
_predictor = None

def get_predictor() -> ActivityPredictor:
    global _predictor
    if _predictor is None:
        _predictor = ActivityPredictor()
    return _predictor
