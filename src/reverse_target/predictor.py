"""
反向寻靶预测服务
基于 ChEMBL 数据库的 Morgan 和 MACCS 指纹进行相似度搜索
"""

import numpy as np
import pandas as pd
import os
from pathlib import Path
from typing import List, Dict, Tuple
from rdkit import Chem
from rdkit import DataStructs
from rdkit.Chem import AllChem, MACCSkeys
import pickle
import threading

from src.reverse_target.config import get_reverse_target_data_dir


def _bitvect_to_numpy_array(bitvect) -> np.ndarray:
    """
    Convert an RDKit ExplicitBitVect to a uint8 NumPy array.

    Some deployment environments expose an older RDKit/Numeric bridge that can
    reject modern NumPy arrays with "Expecting a Numeric array object". The
    explicit GetBit fallback is slower, but query fingerprints are tiny and this
    keeps valid SMILES from failing at runtime.
    """
    num_bits = bitvect.GetNumBits()
    arr = np.zeros((num_bits,), dtype=np.uint8)
    try:
        DataStructs.ConvertToNumpyArray(bitvect, arr)
        return arr
    except (TypeError, ValueError):
        return np.fromiter(
            (1 if bitvect.GetBit(index) else 0 for index in range(num_bits)),
            dtype=np.uint8,
            count=num_bits,
        )


class ReverseTargetPredictor:
    """反向寻靶预测器"""
    
    def __init__(self, data_dir="data/reverse_target"):
        self.data_dir = Path(data_dir)
        
        # 数据文件路径
        self.training_data_path = self._resolve_training_data_path()
        self.morgan_fp_path = self.data_dir / "morgan_fingerprints.npy"
        self.maccs_fp_path = self.data_dir / "maccs_fingerprints.npy"
        self.morgan_popcount_path = self.data_dir / "morgan_popcounts.npy"
        self.maccs_popcount_path = self.data_dir / "maccs_popcounts.npy"
        self.metadata_path = self.data_dir / "fingerprint_metadata.pkl"
        
        # 数据缓存
        self.df = None
        self.morgan_fps = None
        self.maccs_fps = None
        self.metadata = None
        self._loaded = False
        self._load_lock = threading.Lock()

    def _resolve_training_data_path(self) -> Path:
        aligned_path = self.data_dir / "chembl_data_with_fps.tsv"
        legacy_path = self.data_dir / "chembl_training_data.tsv"
        return aligned_path if aligned_path.exists() else legacy_path
    
    def load(self):
        """加载数据和指纹"""
        if self._loaded:
            return

        with self._load_lock:
            if self._loaded:
                return
            
            print("正在加载反向寻靶数据库...")
            
            # 检查文件是否存在
            if not self.training_data_path.exists():
                raise FileNotFoundError(f"训练数据不存在: {self.training_data_path}")
            if not self.morgan_fp_path.exists():
                raise FileNotFoundError(f"Morgan指纹不存在: {self.morgan_fp_path}")
            if not self.maccs_fp_path.exists():
                raise FileNotFoundError(f"MACCS指纹不存在: {self.maccs_fp_path}")
            
            # 加载数据
            self.df = pd.read_csv(self.training_data_path, sep='\t', encoding='utf-8')
            # 使用内存映射模式加载大文件，减少内存占用
            self.morgan_fps = np.load(self.morgan_fp_path, mmap_mode='r')
            self.maccs_fps = np.load(self.maccs_fp_path, mmap_mode='r')
            self._validate_database_shapes()
            
            # 预计算指纹的 popcounts (1的个数) 以加速 Tanimoto 计算
            # 注意：这里会触发全量读取，如果内存不足可能需要分块计算
            print("正在预计算指纹统计信息...")
            self.morgan_popcounts = self._load_or_compute_popcounts(
                self.morgan_fps,
                self.morgan_popcount_path,
            )
            self.maccs_popcounts = self._load_or_compute_popcounts(
                self.maccs_fps,
                self.maccs_popcount_path,
            )
            
            # 加载元数据
            if self.metadata_path.exists():
                with open(self.metadata_path, 'rb') as f:
                    self.metadata = pickle.load(f)
            
            print(f"加载完成: {len(self.df)} 条数据, {self.df['target_name'].nunique()} 个靶点")
            self._loaded = True

    def _validate_database_shapes(self):
        row_count = len(self.df)
        morgan_rows = int(self.morgan_fps.shape[0])
        maccs_rows = int(self.maccs_fps.shape[0])
        if row_count != morgan_rows or row_count != maccs_rows:
            raise ValueError(
                "Reverse-target database row count mismatch: "
                f"{self.training_data_path.name} has {row_count} rows, "
                f"Morgan fingerprints have {morgan_rows}, "
                f"MACCS fingerprints have {maccs_rows}. "
                "Rebuild TSV and fingerprint arrays from the same filtered dataset."
            )

    def _load_or_compute_popcounts(self, fingerprints: np.ndarray, popcount_path: Path) -> np.ndarray:
        if popcount_path.exists():
            popcounts = np.load(popcount_path, mmap_mode='r')
            if popcounts.shape[0] == fingerprints.shape[0]:
                return popcounts

        n_samples = int(fingerprints.shape[0])
        popcounts = np.zeros(n_samples, dtype=np.uint16)
        try:
            chunk_size = int(os.getenv("REVERSE_TARGET_POPCOUNT_CHUNK_SIZE", "50000"))
        except ValueError:
            chunk_size = 50000
        chunk_size = max(1, chunk_size)
        for start in range(0, n_samples, chunk_size):
            end = min(start + chunk_size, n_samples)
            popcounts[start:end] = np.sum(fingerprints[start:end], axis=1, dtype=np.uint32)
        try:
            np.save(popcount_path, popcounts)
        except OSError:
            pass
        return popcounts

    def _combine_similarity_scores(self, morgan_sims: np.ndarray, maccs_sims: np.ndarray) -> np.ndarray:
        try:
            morgan_weight = float(os.getenv("REVERSE_TARGET_MORGAN_WEIGHT", "0.7"))
        except ValueError:
            morgan_weight = 0.7
        morgan_weight = min(1.0, max(0.0, morgan_weight))
        return (morgan_sims * morgan_weight) + (maccs_sims * (1.0 - morgan_weight))
    
    def compute_query_fingerprints(self, smiles: str) -> Tuple[np.ndarray, np.ndarray]:
        """计算查询分子的指纹"""
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            raise ValueError(f"无效的SMILES: {smiles}")
        
        # Morgan 指纹 (ECFP4, 2048 bits)
        morgan_fp = AllChem.GetMorganFingerprintAsBitVect(mol, radius=2, nBits=2048)
        morgan_arr = _bitvect_to_numpy_array(morgan_fp)
        
        # MACCS 指纹 (167 bits, 取后166位)
        maccs_fp = MACCSkeys.GenMACCSKeys(mol)
        maccs_arr_full = _bitvect_to_numpy_array(maccs_fp)
        maccs_arr = maccs_arr_full[1:]  # 去掉第0位，得到166位
        
        return morgan_arr, maccs_arr
    
    def tanimoto_similarity(self, fp1: np.ndarray, fp2: np.ndarray) -> float:
        """计算 Tanimoto 相似度"""
        intersection = np.sum(fp1 & fp2)
        union = np.sum(fp1 | fp2)
        if union == 0:
            return 0.0
        return float(intersection) / float(union)
    
    def batch_tanimoto_similarity(self, query_fp: np.ndarray, database_fps: np.ndarray, db_popcounts: np.ndarray = None) -> np.ndarray:
        """
        批量计算 Tanimoto 相似度 (优化版)
        
        Args:
            query_fp: 查询指纹 (D,)
            database_fps: 数据库指纹 (N, D)
            db_popcounts: 数据库指纹的popcounts (N,)，如果提供则加速计算
            
        Returns:
            相似度数组 (N,)
        """
        if db_popcounts is None:
            # 如果没预计算，退回原始方法 (较慢)
            intersection = np.sum(query_fp & database_fps, axis=1)
            union = np.sum(query_fp | database_fps, axis=1)
            similarities = np.zeros(len(database_fps))
            mask = union > 0
            similarities[mask] = intersection[mask] / union[mask]
            return similarities

        # 优化算法：
        # 1. Intersection = count(Query & DB)
        #    因为 Query 是 0/1 向量，Query & DB 实际上是只保留 Query 为 1 的那些列
        #    所以 Intersection = sum(DB[:, on_bits], axis=1)
        # 2. Union = count(Query) + count(DB) - Intersection
        
        # 获取查询指纹中为 1 的位索引
        on_bits = np.where(query_fp)[0]
        query_popcount = len(on_bits)
        
        if query_popcount == 0:
            return np.zeros(len(database_fps))
            
        # 计算交集 (分块计算以降低内存峰值)
        n_samples = database_fps.shape[0]
        similarities = np.zeros(n_samples, dtype=np.float32)
        chunk_size = 50000
        
        for i in range(0, n_samples, chunk_size):
            end = min(i + chunk_size, n_samples)
            
            # 关键优化：只对 Query 为 1 的列求和
            # 注意：database_fps 是 mmap 数组，切片操作会触发读取
            # 如果 on_bits 很多，这里可能还是慢。但通常指纹是稀疏的。
            chunk_intersection = np.sum(database_fps[i:end][:, on_bits], axis=1)
            
            chunk_union = db_popcounts[i:end] + query_popcount - chunk_intersection
            
            # 避免除零
            mask = chunk_union > 0
            similarities[i:end][mask] = chunk_intersection[mask] / chunk_union[mask]
            
        return similarities
    
    def predict(self, smiles: str, threshold: float = 0.6, top_k: int = 10,
                combine_by_target: bool = True, organism_filter: str = "") -> List[Dict]:
        """
        预测靶点
        
        Args:
            smiles: 查询分子的SMILES
            threshold: 相似度阈值 (0-1)
            top_k: 返回前K个结果
            combine_by_target: 是否按靶点合并结果（取最高相似度）
        
        Returns:
            预测结果列表
        """
        if not self._loaded:
            self.load()
        
        # 计算查询指纹
        query_morgan, query_maccs = self.compute_query_fingerprints(smiles)
        
        # 批量计算相似度 (传入预计算的 popcounts)
        morgan_sims = self.batch_tanimoto_similarity(query_morgan, self.morgan_fps, self.morgan_popcounts)
        maccs_sims = self.batch_tanimoto_similarity(query_maccs, self.maccs_fps, self.maccs_popcounts)
        
        final_sims = self._combine_similarity_scores(morgan_sims, maccs_sims)
        
        # 过滤低于阈值的结果
        mask = final_sims >= threshold
        mask = self._apply_organism_filter(mask, organism_filter)
        
        if not np.any(mask):
            return []
        
        # 构建结果
        results = []
        for idx in np.where(mask)[0]:
            target_name = self.df.iloc[idx]['target_name']
            results.append({
                'target_name': target_name,
                'organism': self.df.iloc[idx]['organism'],
                'canonical_smiles': self.df.iloc[idx]['canonical_smiles'],
                'molecule_chembl_id': self.df.iloc[idx]['molecule_chembl_id'],
                'standard_type': self.df.iloc[idx]['standard_type'],
                'standard_value': float(self.df.iloc[idx]['standard_value']),
                'morgan_similarity': float(morgan_sims[idx]),
                'maccs_similarity': float(maccs_sims[idx]),
                'final_similarity': float(final_sims[idx]),
                'row_index': int(idx),  # 保存原始索引
                # 生成搜索链接
                'chembl_search_url': f"https://www.ebi.ac.uk/chembl/target_report_card/{target_name.replace(' ', '%20')}/",
                'uniprot_search_url': f"https://www.uniprot.org/uniprotkb?query={target_name.replace(' ', '+')}+AND+organism_id:9606"
            })
        
        # 按相似度排序
        results.sort(key=lambda x: x['final_similarity'], reverse=True)
        
        # 如果需要按靶点合并
        if combine_by_target:
            target_map = {}
            target_counts = {}  # 统计每个靶点的相似分子数量
            
            for r in results:
                target = r['target_name']
                if target not in target_counts:
                    target_counts[target] = 0
                target_counts[target] += 1
                
                if target not in target_map or r['final_similarity'] > target_map[target]['final_similarity']:
                    target_map[target] = r
            
            # 添加相似分子数量
            for target, result in target_map.items():
                result['similar_count'] = target_counts[target]
            
            results = list(target_map.values())
            results.sort(key=lambda x: x['final_similarity'], reverse=True)
        else:
            # 如果不合并，每个结果的相似分子数量为1
            for r in results:
                r['similar_count'] = 1
        
        # 返回前K个
        return results[:top_k]

    def predict_batch(self, smiles_list: List[str], threshold: float = 0.6, top_k: int = 10,
                      combine_by_target: bool = True, organism_filter: str = "") -> List[Dict]:
        """
        批量预测靶点
        
        Args:
            smiles_list: 查询分子的SMILES列表
            threshold: 相似度阈值 (0-1)
            top_k: 每个分子返回前K个结果
            combine_by_target: 是否按靶点合并结果
        
        Returns:
            预测结果列表，每个元素包含 'smiles', 'success', 'targets' 或 'error'
        """
        results = []
        for smiles in smiles_list:
            smiles = smiles.strip()
            if not smiles:
                continue
                
            try:
                res = self.predict(smiles, threshold, top_k, combine_by_target, organism_filter=organism_filter)
                results.append({
                    "query_smiles": smiles,
                    "success": True,
                    "targets": res
                })
            except Exception as e:
                results.append({
                    "query_smiles": smiles,
                    "success": False,
                    "error": str(e)
                })
        return results
    
    def get_similar_molecules(
        self,
        smiles: str,
        target_name: str,
        threshold: float = 0.6,
        limit: int = 50,
        organism_filter: str = "",
    ) -> List[Dict]:
        """
        获取特定靶点的相似分子列表
        
        Args:
            smiles: 查询分子的SMILES
            target_name: 靶点名称
            threshold: 相似度阈值
            limit: 返回数量限制
        
        Returns:
            相似分子列表
        """
        if not self._loaded:
            self.load()
        
        # 计算查询指纹
        query_morgan, query_maccs = self.compute_query_fingerprints(smiles)
        
        # 批量计算相似度 (传入预计算的 popcounts)
        morgan_sims = self.batch_tanimoto_similarity(query_morgan, self.morgan_fps, self.morgan_popcounts)
        maccs_sims = self.batch_tanimoto_similarity(query_maccs, self.maccs_fps, self.maccs_popcounts)
        final_sims = self._combine_similarity_scores(morgan_sims, maccs_sims)
        
        # 过滤：相似度 >= threshold 且 target_name 匹配
        target_mask = self.df['target_name'] == target_name
        similarity_mask = final_sims >= threshold
        combined_mask = target_mask & similarity_mask
        combined_mask = self._apply_organism_filter(combined_mask, organism_filter)
        
        if not np.any(combined_mask):
            return []
        
        # 构建结果
        results = []
        for idx in np.where(combined_mask)[0]:
            results.append({
                'molecule_chembl_id': self.df.iloc[idx]['molecule_chembl_id'],
                'canonical_smiles': self.df.iloc[idx]['canonical_smiles'],
                'target_name': self.df.iloc[idx]['target_name'],
                'organism': self.df.iloc[idx]['organism'],
                'standard_type': self.df.iloc[idx]['standard_type'],
                'standard_value': float(self.df.iloc[idx]['standard_value']),
                'morgan_similarity': float(morgan_sims[idx]),
                'maccs_similarity': float(maccs_sims[idx]),
                'final_similarity': float(final_sims[idx])
            })
        
        # 按相似度排序
        results.sort(key=lambda x: x['final_similarity'], reverse=True)
        
        return results[:limit]
    
    def get_stats(self) -> Dict:
        """获取数据库统计信息"""
        if not self._loaded:
            self.load()
        
        return {
            'total_records': len(self.df),
            'unique_molecules': self.df['molecule_chembl_id'].nunique(),
            'unique_targets': self.df['target_name'].nunique(),
            'organisms': self.df['organism'].value_counts().head(10).to_dict(),
            'activity_types': self.df['standard_type'].value_counts().to_dict(),
            'morgan_bits': self.metadata['morgan_bits'] if self.metadata else 2048,
            'maccs_bits': self.metadata['maccs_bits'] if self.metadata else 166
        }

    def get_raw_similar_molecules(
        self,
        smiles: str,
        threshold: float = 0.3,
        limit: int = 100,
        organism_filter: str = "",
    ) -> List[Dict]:
        """
        返回原始相似分子列表（未按靶点聚合），供 3D 药效团精修使用。

        Args:
            smiles    : 查询分子 SMILES
            threshold : 最低 2D 相似度阈值
            limit     : 最多返回多少行（按相似度降序）

        Returns:
            包含 canonical_smiles / target_name / final_similarity 等字段的行列表
        """
        if not self._loaded:
            self.load()

        query_morgan, query_maccs = self.compute_query_fingerprints(smiles)
        morgan_sims = self.batch_tanimoto_similarity(query_morgan, self.morgan_fps, self.morgan_popcounts)
        maccs_sims  = self.batch_tanimoto_similarity(query_maccs,  self.maccs_fps,  self.maccs_popcounts)
        final_sims  = self._combine_similarity_scores(morgan_sims, maccs_sims)

        mask = final_sims >= threshold
        mask = self._apply_organism_filter(mask, organism_filter)
        if not np.any(mask):
            return []

        indices = np.where(mask)[0]
        top_indices = self._select_diverse_candidate_indices(indices, final_sims, limit)

        results = []
        for idx in top_indices:
            results.append({
                'target_name':       str(self.df.iloc[idx]['target_name']),
                'organism':          str(self.df.iloc[idx]['organism']),
                'canonical_smiles':  str(self.df.iloc[idx]['canonical_smiles']),
                'molecule_chembl_id':str(self.df.iloc[idx]['molecule_chembl_id']),
                'standard_type':     str(self.df.iloc[idx]['standard_type']),
                'standard_value':    float(self.df.iloc[idx]['standard_value']),
                'morgan_similarity': float(morgan_sims[idx]),
                'maccs_similarity':  float(maccs_sims[idx]),
                'final_similarity':  float(final_sims[idx]),
            })
        return results

    def _select_diverse_candidate_indices(
        self,
        indices: np.ndarray,
        final_sims: np.ndarray,
        limit: int,
        per_target_limit: int | None = None,
    ) -> np.ndarray:
        if len(indices) == 0 or limit <= 0:
            return np.array([], dtype=int)
        if self.df is None or "target_name" not in self.df.columns:
            order = np.argsort(final_sims[indices])[::-1][:limit]
            return indices[order]

        per_target = per_target_limit
        if per_target is None:
            try:
                per_target = int(os.getenv("REVERSE_TARGET_RAW_PER_TARGET_LIMIT", "1"))
            except ValueError:
                per_target = 1
        per_target = max(1, per_target)

        ranked = indices[np.argsort(final_sims[indices])[::-1]]
        selected: List[int] = []
        deferred: List[int] = []
        target_counts: Dict[str, int] = {}

        for idx in ranked:
            target = str(self.df.iloc[int(idx)]["target_name"])
            count = target_counts.get(target, 0)
            if count < per_target:
                selected.append(int(idx))
                target_counts[target] = count + 1
                if len(selected) >= limit:
                    return np.array(selected, dtype=int)
            else:
                deferred.append(int(idx))

        for idx in deferred:
            selected.append(idx)
            if len(selected) >= limit:
                break

        return np.array(selected, dtype=int)

    def _apply_organism_filter(self, mask: np.ndarray, organism_filter: str = "") -> np.ndarray:
        organism_filter = (organism_filter or "").strip()
        if not organism_filter:
            return mask
        if self.df is None or "organism" not in self.df.columns:
            return mask
        organism_values = self.df["organism"].fillna("").astype(str).str.lower()
        filter_value = organism_filter.lower()
        if filter_value in {"human", "homo sapiens"}:
            organism_mask = organism_values.isin({"human", "homo sapiens"})
        else:
            organism_mask = organism_values.str.contains(filter_value, regex=False)
        return mask & organism_mask.to_numpy()



# 全局单例
_predictor = None
_predictor_lock = threading.Lock()


def get_predictor() -> ReverseTargetPredictor:
    """获取全局预测器实例"""
    global _predictor
    if _predictor is None:
        with _predictor_lock:
            if _predictor is None:
                predictor = ReverseTargetPredictor(data_dir=get_reverse_target_data_dir())
                predictor.load()
                _predictor = predictor
    return _predictor


def _aggregate_by_target(
    candidates: List[Dict],
    top_k: int = 10,
    score_field: str = "final_3d_score",
) -> List[Dict]:
    """
    按靶点聚合候选分子，取每个靶点的最高分代表，返回 top_k 靶点。
    供 3D 级联 API 调用。
    """
    target_map: Dict[str, Dict] = {}
    target_counts: Dict[str, int] = {}

    for r in candidates:
        target = r.get('target_name', '')
        score  = r.get(score_field, r.get('final_similarity', 0.0))

        target_counts[target] = target_counts.get(target, 0) + 1

        if target not in target_map or score > target_map[target].get(score_field, 0):
            target_map[target] = r

    for target, result in target_map.items():
        result['similar_count'] = target_counts[target]
        # 生成搜索链接
        result.setdefault(
            'chembl_search_url',
            f"https://www.ebi.ac.uk/chembl/target_report_card/{target.replace(' ', '%20')}/"
        )
        result.setdefault(
            'uniprot_search_url',
            f"https://www.uniprot.org/uniprotkb?query={target.replace(' ', '+')}+AND+organism_id:9606"
        )

    results = list(target_map.values())
    results.sort(key=lambda x: x.get(score_field, 0), reverse=True)
    return results[:top_k]
