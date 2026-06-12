"""
3D 药效团精修模块 (Pharmacophore Refiner)

基于 RDKit 的 3D 药效团反向寻靶精修模块，采用"2D 筛选 + 3D 精修"级联策略:

1. 第一级: 现有 Morgan 2D 指纹快速筛选 Top-N 候选分子 (已在 predictor.py 中实现)
2. 第二级 (本模块): 对候选分子进行 3D 构象生成 + 药效团特征提取 + 空间匹配打分

药效团特征类型 (PharmGKB/RDKit 标准):
  - Donor     : HBD 氢键给体
  - Acceptor  : HBA 氢键受体
  - Hydrophobe: 疏水中心
  - Aromatic  : 芳香环
  - PosIonizable  : 正可电离中心
  - NegIonizable  : 负可电离中心
"""

from __future__ import annotations

import os
import time
import logging
import hashlib
import pickle
import itertools
from functools import lru_cache
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import numpy as np

from src.reverse_target.config import get_pharm3d_cache_dir

try:
    from rdkit import Chem, RDConfig
    from rdkit.Chem import AllChem, ChemicalFeatures
    from rdkit.Chem.rdMolDescriptors import CalcTPSA, CalcNumRotatableBonds, CalcNumHBD, CalcNumHBA
    from rdkit.Chem.Descriptors import MolWt
    from rdkit.Chem.Crippen import MolLogP
except ModuleNotFoundError:
    Chem = None
    RDConfig = None
    AllChem = None
    ChemicalFeatures = None
    CalcTPSA = CalcNumRotatableBonds = CalcNumHBD = CalcNumHBA = None
    MolWt = MolLogP = None

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
#   全局药效团特征工厂 (单例)
# ─────────────────────────────────────────
_FDEF_PATH = os.path.join(RDConfig.RDDataDir, "BaseFeatures.fdef") if RDConfig else ""
_FEATURE_FACTORY = None

def _require_rdkit():
    if Chem is None:
        raise ImportError("RDKit is required for 3D pharmacophore generation")


def _get_factory():
    """懒加载特征工厂（线程安全单例）"""
    _require_rdkit()
    global _FEATURE_FACTORY
    if _FEATURE_FACTORY is None:
        if not os.path.exists(_FDEF_PATH):
            raise FileNotFoundError(f"RDKit BaseFeatures.fdef not found: {_FDEF_PATH}")
        _FEATURE_FACTORY = ChemicalFeatures.BuildFeatureFactory(_FDEF_PATH)
    return _FEATURE_FACTORY


# ─────────────────────────────────────────
#   缓存目录（避免重复计算相同分子的 3D 构象）
# ─────────────────────────────────────────
_CACHE_DIR: Optional[Path] = None

def _get_cache_dir() -> Path:
    """懒加载缓存目录，以当前工作目录为基准"""
    global _CACHE_DIR
    if _CACHE_DIR is None:
        _CACHE_DIR = get_pharm3d_cache_dir()
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR


def _smiles_hash(smiles: str) -> str:
    return hashlib.md5(smiles.encode()).hexdigest()[:12]


def _try_load_cache(smiles: str) -> Optional[Dict]:
    """尝试从磁盘缓存加载药效团数据"""
    try:
        h = _smiles_hash(smiles)
        cache_file = _get_cache_dir() / f"{h}.pkl"
        if cache_file.exists():
            with open(cache_file, "rb") as f:
                return pickle.load(f)
    except Exception:
        pass
    return None


def _save_cache(smiles: str, data: Dict):
    tmp_file = None
    """保存药效团数据到磁盘缓存"""
    try:
        h = _smiles_hash(smiles)
        cache_file = _get_cache_dir() / f"{h}.pkl"
        tmp_file = cache_file.with_name(f"{cache_file.name}.{os.getpid()}.tmp")
        with open(tmp_file, "wb") as f:
            pickle.dump(data, f)
        os.replace(tmp_file, cache_file)
    except Exception:
        pass
    finally:
        if tmp_file is not None:
            try:
                if tmp_file.exists():
                    tmp_file.unlink()
            except Exception:
                pass


# ─────────────────────────────────────────
#   特征颜色映射 (用于前端 SVG 可视化)
# ─────────────────────────────────────────
FEATURE_COLORS = {
    "Donor":        {"color": "#3b82f6", "icon": "💧", "label": "HBD 氢键给体",   "abbr": "HBD"},
    "Acceptor":     {"color": "#f59e0b", "icon": "🔶", "label": "HBA 氢键受体",   "abbr": "HBA"},
    "Hydrophobe":   {"color": "#10b981", "icon": "🟢", "label": "疏水中心",       "abbr": "HYD"},
    "Aromatic":     {"color": "#8b5cf6", "icon": "⬡",  "label": "芳香环",         "abbr": "ARO"},
    "NegIonizable": {"color": "#ef4444", "icon": "➖", "label": "负电荷中心",     "abbr": "NEG"},
    "PosIonizable": {"color": "#06b6d4", "icon": "➕", "label": "正电荷中心",     "abbr": "POS"},
    "LumpedHydrophobe": {"color": "#10b981", "icon": "🟢", "label": "疏水中心 (大)", "abbr": "HYD"},
}

FEATURE_PRIORITY = ["Aromatic", "Donor", "Acceptor", "Hydrophobe", "LumpedHydrophobe",
                     "NegIonizable", "PosIonizable"]


# ─────────────────────────────────────────
#   核心功能：3D 构象生成 + 药效团提取
# ─────────────────────────────────────────
def generate_3d_conformer(mol: Chem.Mol, num_confs: int = 5) -> Optional[Chem.Mol]:
    """
    生成优化后的 3D 构象，返回带最优构象的分子 (含氢)，失败返回 None。

    策略:
    1. Sanitize + AddHs
    2. ETKDGv3 多构象采样
    3. MMFF94 能量优化，选择能量最低构象
    4. 若 ETKDGv3 失败，降级到 ETKDG
    """
    _require_rdkit()
    try:
        # 确保分子已 Sanitize
        mol = Chem.RWMol(mol)
        Chem.SanitizeMol(mol)
        mol_h = Chem.AddHs(mol)

        # ETKDGv3 多构象采样
        params = AllChem.ETKDGv3()
        params.randomSeed = 42
        params.numThreads = 1
        params.enforceChirality = True

        conf_ids = list(AllChem.EmbedMultipleConfs(mol_h, numConfs=num_confs, params=params))

        if len(conf_ids) == 0:
            # 降级：单构象 ETKDG
            ret = AllChem.EmbedMolecule(mol_h, randomSeed=42)
            if ret < 0:
                return None
            conf_ids = [0]

        # MMFF94 力场优化，选择能量最低构象
        res = AllChem.MMFFOptimizeMoleculeConfs(mol_h, numThreads=1)
        # res: list of (converged, energy) per conf
        energies = [(e, idx) for idx, (converged, e) in enumerate(res) if e > -1e9]
        if energies:
            best_idx = sorted(energies)[0][1]
        else:
            best_idx = 0

        # 把最优构象移到 0 号
        if best_idx != 0 and mol_h.GetNumConformers() > best_idx:
            best_conf = Chem.Conformer(mol_h.GetConformer(best_idx))
            mol_h.RemoveAllConformers()
            mol_h.AddConformer(best_conf, assignId=True)
        elif mol_h.GetNumConformers() > 1:
            # 只保留 0 号
            keep = Chem.Conformer(mol_h.GetConformer(0))
            mol_h.RemoveAllConformers()
            mol_h.AddConformer(keep, assignId=True)

        return mol_h

    except Exception as e:
        logger.warning(f"3D conformer generation failed: {e}")
        return None


def extract_pharmacophore_features(mol_3d: Chem.Mol) -> List[Dict]:
    """
    提取 3D 药效团特征，返回特征列表。

    每个特征字典包含:
        family  : 特征大类 (Donor / Acceptor / ...)
        type    : 特征子类 (SingleAtomDonor / ...)
        pos     : [x, y, z] 3D 坐标
        atom_ids: 相关原子索引列表
        color   : 前端显示颜色
        label   : 中文名称
        icon    : emoji 图标
    """
    factory = _get_factory()
    features = factory.GetFeaturesForMol(mol_3d)

    result = []
    for feat in features:
        family = feat.GetFamily()
        ftype = feat.GetType()
        pos = feat.GetPos()
        atom_ids = list(feat.GetAtomIds())

        meta = FEATURE_COLORS.get(family, {"color": "#94a3b8", "icon": "◯", "label": family, "abbr": family[:3].upper()})

        result.append({
            "family":   family,
            "type":     ftype,
            "pos":      [round(pos.x, 3), round(pos.y, 3), round(pos.z, 3)],
            "atom_ids": atom_ids,
            "color":    meta["color"],
            "label":    meta["label"],
            "icon":     meta["icon"],
            "abbr":     meta["abbr"],
        })

    return result


def get_molecule_pharmacophore(smiles: str) -> Dict:
    """
    完整流程：SMILES → 3D → 药效团特征

    返回包含:
        success       : bool
        features      : 药效团特征列表
        feature_counts: 各类特征数量统计
        mol_block     : MolBlock 字符串 (供前端 3Dmol.js 渲染)
        properties    : 基础理化性质
        error         : 失败时的错误信息
    """
    # 尝试磁盘缓存
    cached = _try_load_cache(smiles)
    if cached:
        logger.debug(f"Loaded pharmacophore from cache: {smiles[:20]}...")
        return cached

    try:
        _require_rdkit()
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return {"success": False, "error": f"无效的 SMILES: {smiles}"}

        # 3D 构象生成
        t0 = time.time()
        mol_3d = generate_3d_conformer(mol)
        t1 = time.time()

        if mol_3d is None:
            return {"success": False, "error": "3D 构象生成失败，分子可能过于复杂"}

        # 药效团特征提取
        features = extract_pharmacophore_features(mol_3d)

        # 特征统计
        feature_counts = {}
        for f in features:
            fam = f["family"]
            if fam not in feature_counts:
                feature_counts[fam] = 0
            feature_counts[fam] += 1

        # MolBlock (供 3Dmol.js 渲染)
        mol_block = Chem.MolToMolBlock(mol_3d)

        # 基础理化性质
        mol_no_h = Chem.RemoveHs(mol_3d)
        properties = {
            "MW":   round(MolWt(mol_no_h), 2),
            "LogP": round(MolLogP(mol_no_h), 2),
            "TPSA": round(CalcTPSA(mol_no_h), 2),
            "HBD":  CalcNumHBD(mol_no_h),
            "HBA":  CalcNumHBA(mol_no_h),
            "RotBonds": CalcNumRotatableBonds(mol_no_h),
        }

        result = {
            "success":        True,
            "features":       features,
            "feature_counts": feature_counts,
            "mol_block":      mol_block,
            "properties":     properties,
            "conformer_time_ms": round((t1 - t0) * 1000, 1),
        }

        _save_cache(smiles, result)
        return result

    except Exception as e:
        logger.error(f"Pharmacophore extraction failed for {smiles[:30]}: {e}")
        return {"success": False, "error": str(e)}


# ─────────────────────────────────────────
#   3D 药效团打分：特征向量匹配
# ─────────────────────────────────────────
def _feature_vector(features: List[Dict]) -> Dict[str, int]:
    """将特征列表转化为特征类型–数量字典（用于快速比较）"""
    vec = {}
    for f in features:
        k = f["family"]
        vec[k] = vec.get(k, 0) + 1
    return vec


def pharmacophore_similarity(feats_query: List[Dict], feats_hit: List[Dict]) -> float:
    """
    计算两分子之间的药效团相似度 (0.0 ~ 1.0)

    方法：加权特征向量 Tanimoto 相似度
    - 不依赖空间对齐（对齐计算量大，网页端不适合）
    - 利用特征类型的匹配程度打分
    - 权重：Aromatic > Donor/Acceptor > Hydrophobe > Ionizable
    """
    WEIGHTS = {
        "Aromatic":         3.0,
        "Donor":            2.5,
        "Acceptor":         2.5,
        "Hydrophobe":       1.5,
        "LumpedHydrophobe": 1.5,
        "PosIonizable":     2.0,
        "NegIonizable":     2.0,
    }

    vec_q = _feature_vector(feats_query)
    vec_h = _feature_vector(feats_hit)
    all_keys = set(vec_q.keys()) | set(vec_h.keys())

    numerator   = 0.0
    denominator = 0.0

    for key in all_keys:
        w = WEIGHTS.get(key, 1.0)
        q_val = vec_q.get(key, 0)
        h_val = vec_h.get(key, 0)

        numerator   += w * min(q_val, h_val)
        denominator += w * max(q_val, h_val)

    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _matched_feature_points(feats_query: List[Dict], feats_hit: List[Dict]) -> Tuple[np.ndarray, np.ndarray]:
    """按药效团类型收集可叠合的特征点对。"""
    query_points = []
    hit_points = []

    for fam in FEATURE_PRIORITY:
        q_pts = [
            np.array(f["pos"], dtype=float)
            for f in feats_query
            if f.get("family") == fam and f.get("pos") is not None
        ]
        h_pts = [
            np.array(f["pos"], dtype=float)
            for f in feats_hit
            if f.get("family") == fam and f.get("pos") is not None
        ]
        if not q_pts or not h_pts:
            continue

        for q_pt, h_pt in zip(q_pts[: min(len(q_pts), len(h_pts))], h_pts[: min(len(q_pts), len(h_pts))]):
            query_points.append(q_pt)
            hit_points.append(h_pt)

    if not query_points:
        return np.empty((0, 3)), np.empty((0, 3))

    return np.vstack(query_points), np.vstack(hit_points)


def _indexed_feature_points(features: List[Dict]) -> List[Dict]:
    points = []
    for idx, feature in enumerate(features):
        if feature.get("pos") is None or feature.get("family") is None:
            continue
        try:
            pos = np.array(feature["pos"], dtype=float)
        except (TypeError, ValueError):
            continue
        if pos.shape != (3,) or not np.isfinite(pos).all():
            continue
        points.append({"index": idx, "family": feature["family"], "pos": pos})
    return points


def _kabsch_transform(query_points: np.ndarray, hit_points: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    q_center = query_points.mean(axis=0)
    h_center = hit_points.mean(axis=0)
    q_centered = query_points - q_center
    h_centered = hit_points - h_center

    covariance = h_centered.T @ q_centered
    u, _, vt = np.linalg.svd(covariance)
    rotation = u @ vt

    if np.linalg.det(rotation) < 0:
        u[:, -1] *= -1
        rotation = u @ vt

    return rotation, h_center, q_center


def _apply_transform(point: np.ndarray, rotation: np.ndarray, hit_center: np.ndarray, query_center: np.ndarray) -> np.ndarray:
    return (point - hit_center) @ rotation + query_center


def _rmsd(query_points: np.ndarray, hit_points: np.ndarray) -> float:
    if len(query_points) == 0:
        return float("inf")
    return float(np.sqrt(np.mean(np.sum((query_points - hit_points) ** 2, axis=1))))


def _score_transformed_alignment(
    query_features: List[Dict],
    hit_features: List[Dict],
    rotation: np.ndarray,
    hit_center: np.ndarray,
    query_center: np.ndarray,
    distance_cutoff: float,
) -> Dict:
    transformed_hits = [
        {
            **hit,
            "aligned_pos": _apply_transform(hit["pos"], rotation, hit_center, query_center),
        }
        for hit in hit_features
    ]

    matched = []
    used_hits = set()

    for query in query_features:
        same_family_hits = [
            hit for hit in transformed_hits
            if hit["family"] == query["family"] and hit["index"] not in used_hits
        ]
        if not same_family_hits:
            continue

        best_hit = min(
            same_family_hits,
            key=lambda hit: float(np.linalg.norm(query["pos"] - hit["aligned_pos"])),
        )
        distance = float(np.linalg.norm(query["pos"] - best_hit["aligned_pos"]))
        if distance <= distance_cutoff:
            used_hits.add(best_hit["index"])
            matched.append({
                "query_index": query["index"],
                "hit_index": best_hit["index"],
                "family": query["family"],
                "distance": round(distance, 4),
            })

    possible_count = min(len(query_features), len(hit_features))
    if not matched or possible_count == 0:
        return {
            "success": False,
            "score": 0.0,
            "alignment_rmsd": None,
            "matched_pair_count": 0,
            "possible_pair_count": possible_count,
            "alignment_coverage": 0.0,
            "alignment_pairs": [],
        }

    distances = np.array([pair["distance"] for pair in matched], dtype=float)
    rmsd = float(np.sqrt(np.mean(distances ** 2)))
    coverage = len(matched) / possible_count
    score = float(np.exp(-rmsd / 1.5) * coverage)

    return {
        "success": True,
        "score": round(score, 4),
        "alignment_rmsd": round(rmsd, 4),
        "matched_pair_count": len(matched),
        "possible_pair_count": possible_count,
        "alignment_coverage": round(coverage, 4),
        "alignment_pairs": matched,
    }


def _pairwise_distance_signature(points: np.ndarray) -> np.ndarray:
    """生成不依赖平移和旋转的点间距离签名。"""
    if len(points) < 2:
        return np.array([], dtype=float)

    distances = []
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            distances.append(float(np.linalg.norm(points[i] - points[j])))
    return np.array(sorted(distances), dtype=float)


def feature_distribution_score(feats_query: List[Dict], feats_hit: List[Dict]) -> float:
    """
    药效团空间分布启发式评分。

    该分数只比较同类药效团特征点之间的距离分布，不依赖坐标原点，
    也不声称完成了严格的 3D 叠合。保留它用于少于 3 个匹配特征点时的降级。
    """
    q_pts, h_pts = _matched_feature_points(feats_query, feats_hit)
    if len(q_pts) == 0 or len(h_pts) == 0:
        return 0.0

    if len(q_pts) == 1:
        return pharmacophore_similarity(feats_query, feats_hit)

    q_sig = _pairwise_distance_signature(q_pts)
    h_sig = _pairwise_distance_signature(h_pts)
    if len(q_sig) == 0 or len(h_sig) == 0:
        return 0.0

    count = min(len(q_sig), len(h_sig))
    diff = np.abs(q_sig[:count] - h_sig[:count])
    scale = max(float(np.mean(q_sig[:count] + h_sig[:count]) / 2.0), 1.0)
    return round(float(np.exp(-float(np.mean(diff)) / scale)), 4)


def align_pharmacophore_features(
    feats_query: List[Dict],
    feats_hit: List[Dict],
    max_anchor_combinations: int = 2500,
    distance_cutoff: float = 1.8,
) -> Dict:
    """
    自动寻找同类药效团特征点配对，并执行轻量 3D 刚性叠合。

    实现思路：
    - 只允许同 family 的特征互相匹配；
    - 枚举少量 3 点 anchor 组合，用 Kabsch 算法做刚性叠合；
    - 在叠合后的坐标系中按同 family 最近邻计算覆盖率和 RMSD；
    - 返回 score、RMSD、匹配点数量和配对明细。
    """
    query_features = _indexed_feature_points(feats_query)
    hit_features = _indexed_feature_points(feats_hit)
    possible_count = min(len(query_features), len(hit_features))

    if possible_count < 3:
        distribution = feature_distribution_score(feats_query, feats_hit)
        return {
            "success": False,
            "score": distribution,
            "alignment_rmsd": None,
            "matched_pair_count": possible_count,
            "possible_pair_count": possible_count,
            "alignment_coverage": 0.0,
            "alignment_pairs": [],
            "fallback": "feature_distribution_score",
        }

    candidate_pairs = [
        (q, h)
        for q in query_features
        for h in hit_features
        if q["family"] == h["family"]
    ]
    if len(candidate_pairs) < 3:
        distribution = feature_distribution_score(feats_query, feats_hit)
        return {
            "success": False,
            "score": distribution,
            "alignment_rmsd": None,
            "matched_pair_count": len(candidate_pairs),
            "possible_pair_count": possible_count,
            "alignment_coverage": 0.0,
            "alignment_pairs": [],
            "fallback": "feature_distribution_score",
        }

    best = None
    checked = 0

    for anchors in itertools.combinations(candidate_pairs, 3):
        query_ids = [q["index"] for q, _ in anchors]
        hit_ids = [h["index"] for _, h in anchors]
        if len(set(query_ids)) < 3 or len(set(hit_ids)) < 3:
            continue

        q_anchor = np.vstack([q["pos"] for q, _ in anchors])
        h_anchor = np.vstack([h["pos"] for _, h in anchors])
        rotation, hit_center, query_center = _kabsch_transform(q_anchor, h_anchor)

        result = _score_transformed_alignment(
            query_features,
            hit_features,
            rotation,
            hit_center,
            query_center,
            distance_cutoff=distance_cutoff,
        )
        if result["success"] and (
            best is None
            or result["score"] > best["score"]
            or (
                result["score"] == best["score"]
                and (result["alignment_rmsd"] or float("inf")) < (best["alignment_rmsd"] or float("inf"))
            )
        ):
            best = result

        checked += 1
        if checked >= max_anchor_combinations:
            break

    if best is None:
        distribution = feature_distribution_score(feats_query, feats_hit)
        return {
            "success": False,
            "score": distribution,
            "alignment_rmsd": None,
            "matched_pair_count": 0,
            "possible_pair_count": possible_count,
            "alignment_coverage": 0.0,
            "alignment_pairs": [],
            "fallback": "feature_distribution_score",
        }

    best["anchor_combinations_checked"] = checked
    best["score_method"] = "feature_point_kabsch_alignment"
    return best


def aligned_pharmacophore_score(feats_query: List[Dict], feats_hit: List[Dict]) -> float:
    """
    基于同类药效团特征点的轻量 3D 刚性叠合评分。

    当匹配特征点不少于 3 个时，使用 Kabsch 算法将 hit 特征点旋转/平移到
    query 特征点坐标系，再把 RMSD 映射为 0~1 分数。匹配点不足时降级为
    feature_distribution_score。
    """
    return align_pharmacophore_features(feats_query, feats_hit)["score"]


def spatial_alignment_score(feats_query: List[Dict], feats_hit: List[Dict]) -> float:
    """兼容旧字段名；实际返回轻量 3D 特征点叠合评分。"""
    return aligned_pharmacophore_score(feats_query, feats_hit)


def compute_pharm3d_score(smiles_query: str, smiles_hit: str) -> Dict:
    """
    计算两分子的全量 3D 药效团匹配分数

    返回：
        pharm_similarity : 药效团特征向量相似度
        alignment_score  : 轻量 3D 特征点叠合分数
        combined_3d_score: 综合 3D 分数 (0.6 * pharm + 0.4 * alignment)
        query_features   : 查询分子药效团特征列表
        hit_features     : Hit 分子药效团特征列表
    """
    result_q = get_molecule_pharmacophore(smiles_query)
    result_h = get_molecule_pharmacophore(smiles_hit)

    if not result_q["success"]:
        return {"success": False, "error": f"查询分子 3D 失败: {result_q.get('error')}"}
    if not result_h["success"]:
        return {"success": False, "error": f"Hit 分子 3D 失败: {result_h.get('error')}"}

    feats_q = result_q["features"]
    feats_h = result_h["features"]

    pharm_sim = pharmacophore_similarity(feats_q, feats_h)
    alignment = align_pharmacophore_features(feats_q, feats_h)
    alignment_s = alignment["score"]
    combined  = round(0.6 * pharm_sim + 0.4 * alignment_s, 4)

    return {
        "success":          True,
        "pharm_similarity": pharm_sim,
        "alignment_score":  alignment_s,
        "spatial_score":    alignment_s,
        "alignment_rmsd":   alignment.get("alignment_rmsd"),
        "alignment_pairs":  alignment.get("alignment_pairs", []),
        "alignment_coverage": alignment.get("alignment_coverage", 0.0),
        "score_method":     alignment.get("score_method", "feature_distribution_score"),
        "combined_3d_score": combined,
        "query_features":   feats_q,
        "hit_features":     feats_h,
        "query_mol_block":  result_q["mol_block"],
        "hit_mol_block":    result_h["mol_block"],
        "query_properties": result_q["properties"],
        "hit_properties":   result_h["properties"],
        "query_feature_counts": result_q["feature_counts"],
        "hit_feature_counts":   result_h["feature_counts"],
    }


# ─────────────────────────────────────────
#   批量精修：对 Top-N 候选分子做 3D 重新排序
# ─────────────────────────────────────────
def refine_with_pharmacophore(
    query_smiles: str,
    candidates: List[Dict],
    max_to_refine: int = 100,
    alpha_2d: float = 0.4,
    alpha_3d: float = 0.6,
    timeout_seconds: float = 30.0,
) -> List[Dict]:
    """
    对 2D 预筛选的候选分子做 3D 药效团精修，重新排序。

    Args:
        query_smiles   : 查询分子 SMILES
        candidates     : predictor.predict() 的输出列表 (按 final_similarity 排序)
        max_to_refine  : 最多精修多少个分子
        alpha_2d       : 最终综合评分中 2D 相似度的权重
        alpha_3d       : 最终综合评分中 3D 药效团分数的权重
        timeout_seconds: 整批超时时间（秒），超时后仅输出已计算的结果

    Returns:
        精修后的候选列表，新增字段：
            pharm_combined_3d  : 综合 3D 分数
            pharm_similarity   : 药效团向量相似度
            alignment_score    : 轻量 3D 特征点叠合分数
            spatial_score      : 兼容旧前端字段，等同于 alignment_score
            final_3d_score     : 融合 2D + 3D 的最终分数
            pharm_features     : Hit 分子药效团特征（用于前端高亮）
    """
    # 先获取查询分子的药效团
    q_result = get_molecule_pharmacophore(query_smiles)
    if not q_result["success"]:
        logger.warning(f"Query pharmacophore failed: {q_result.get('error')}")
        # 退化为 2D 结果
        for c in candidates:
            c["final_3d_score"] = c.get("final_similarity", 0.0)
            c["pharm_combined_3d"] = None
            c["pharm_similarity"] = None
            c["alignment_score"] = None
            c["spatial_score"] = None
            c["pharm_error"] = q_result.get("error", "")
        return candidates

    feats_q = q_result["features"]
    refined = []
    t_start = time.time()

    to_process = candidates[:max_to_refine]

    for index, cand in enumerate(to_process):
        if time.time() - t_start > timeout_seconds:
            logger.warning(
                "Pharmacophore refinement timeout reached after %s/%s candidates; "
                "returning partial 3D results with 2D fallback for remaining candidates",
                len(refined),
                len(to_process),
            )
            # 超时后一次性收尾，避免每个候选分子重复刷 warning。
            for pending in to_process[index:]:
                pending["final_3d_score"] = pending.get("final_similarity", 0.0)
                pending["pharm_combined_3d"] = None
                pending["pharm_similarity"] = None
                pending["alignment_score"] = None
                pending["spatial_score"] = None
                pending["pharm_features"] = []
                pending["pharm_refinement_status"] = "timeout_fallback"
                pending["pharm_error"] = "3D pharmacophore refinement timed out before this candidate was processed"
                refined.append(pending)
            break

        hit_smiles = cand.get("canonical_smiles", "")
        if not hit_smiles:
            cand["final_3d_score"] = cand.get("final_similarity", 0.0)
            cand["pharm_combined_3d"] = None
            cand["pharm_refinement_status"] = "missing_smiles"
            refined.append(cand)
            continue

        h_result = get_molecule_pharmacophore(hit_smiles)

        if not h_result["success"]:
            # 3D 失败，仅用 2D 分数
            cand["final_3d_score"] = cand.get("final_similarity", 0.0)
            cand["pharm_combined_3d"] = None
            cand["pharm_similarity"] = None
            cand["alignment_score"] = None
            cand["spatial_score"] = None
            cand["pharm_features"] = []
            cand["pharm_error"] = h_result.get("error", "")
            cand["pharm_refinement_status"] = "fallback"
        else:
            feats_h = h_result["features"]
            pharm_sim = pharmacophore_similarity(feats_q, feats_h)
            alignment = align_pharmacophore_features(feats_q, feats_h)
            alignment_s = alignment["score"]
            combined_3d = round(0.6 * pharm_sim + 0.4 * alignment_s, 4)

            # 融合 2D + 3D 分数
            score_2d = cand.get("final_similarity", 0.0)
            final_3d = round(alpha_2d * score_2d + alpha_3d * combined_3d, 4)

            cand["pharm_combined_3d"]  = combined_3d
            cand["pharm_similarity"]   = pharm_sim
            cand["alignment_score"]    = alignment_s
            cand["spatial_score"]      = alignment_s
            cand["alignment_rmsd"]     = alignment.get("alignment_rmsd")
            cand["alignment_pairs"]    = alignment.get("alignment_pairs", [])
            cand["alignment_coverage"] = alignment.get("alignment_coverage", 0.0)
            cand["pharm_score_method"] = alignment.get("score_method", "feature_distribution_score")
            cand["final_3d_score"]     = final_3d
            cand["pharm_features"]     = feats_h
            cand["pharm_feature_counts"] = h_result["feature_counts"]
            cand["pharm_refinement_status"] = "refined"

        refined.append(cand)

    # 按 final_3d_score 重新排序
    refined.sort(key=lambda x: x.get("final_3d_score", 0.0), reverse=True)

    # 把剩余（未精修，在 max_to_refine 之外）的候选追加到末尾
    if len(candidates) > max_to_refine:
        rest = candidates[max_to_refine:]
        for c in rest:
            c["final_3d_score"] = c.get("final_similarity", 0.0)
            c["pharm_combined_3d"] = None
            c["pharm_similarity"] = None
            c["alignment_score"] = None
            c["spatial_score"] = None
            c["pharm_features"] = []
            c["pharm_refinement_status"] = "not_refined"
            refined.append(c)

    logger.info(f"Pharmacophore refinement done: {len(refined)} candidates in {time.time()-t_start:.1f}s")
    return refined
