#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
反向寻靶子页面模块（Python）
- 基于 2D Tanimoto 相似度 + Morgan(半径=2, 2048位) + MACCS(166位)
- 从CSV加载分子库（字段：canonical_smiles, target_name）
- 提供 predict_targets(smiles: str, threshold=0.6, top_k=10) 接口
- 结果返回 pandas.DataFrame，同时在命令行打印
- 包含可直接运行的示例主函数
"""

from __future__ import annotations

import os
import sys
import traceback
from dataclasses import dataclass
from typing import Optional, List

import pandas as pd

try:
    from rdkit import Chem
    from rdkit.Chem import AllChem
    from rdkit.Chem import MACCSkeys
    from rdkit import DataStructs
except Exception as e:
    # 延迟在运行时抛出更清晰的错误
    Chem = AllChem = MACCSkeys = DataStructs = None  # type: ignore


DEFAULT_CSV_PATH = os.path.join(os.path.dirname(__file__), "targets_dataset.csv")


@dataclass
class MoleculeEntry:
    smiles: str
    target_name: str
    morgan_fp: Optional[object]
    maccs_fp: Optional[object]


class TargetDatabase:
    """负责加载CSV并缓存指纹。"""

    def __init__(self, csv_path: str):
        self.csv_path = csv_path
        self.df: Optional[pd.DataFrame] = None
        self.entries: List[MoleculeEntry] = []

    def _ensure_rdkit(self):
        if any(x is None for x in (Chem, AllChem, MACCSkeys, DataStructs)):
            raise RuntimeError(
                "RDKit 未正确安装或导入失败。请确保已安装 rdkit-pypi，并与当前 Python 兼容。"
            )

    def load(self) -> None:
        self._ensure_rdkit()
        if not os.path.exists(self.csv_path):
            raise FileNotFoundError(f"数据集CSV不存在: {self.csv_path}")

        df = pd.read_csv(self.csv_path)
        # 只保留必要列
        required_cols = ["canonical_smiles", "target_name"]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise ValueError(f"CSV缺少必要列: {missing}")

        df = df[required_cols].copy()
        df = df.dropna(subset=["canonical_smiles", "target_name"])  # 基本清洗
        self.df = df

        # 预计算指纹并缓存
        self.entries = []
        for _, row in df.iterrows():
            smi = str(row["canonical_smiles"]).strip()
            tgt = str(row["target_name"]).strip()
            mol = None
            morgan_fp = None
            maccs_fp = None
            try:
                mol = Chem.MolFromSmiles(smi)
                if mol is None:
                    continue  # 跳过非法SMILES
                # Morgan 指纹：半径=2，长度=2048
                morgan_fp = AllChem.GetMorganFingerprintAsBitVect(mol, radius=2, nBits=2048)
                # MACCS 指纹：166位
                maccs_fp = MACCSkeys.GenMACCSKeys(mol)
            except Exception:
                # 保持健壮性：跳过异常条目
                continue

            self.entries.append(MoleculeEntry(smi, tgt, morgan_fp, maccs_fp))

    def is_loaded(self) -> bool:
        return bool(self.entries)


# 全局单例（可被 predict_targets 使用）
_DB: Optional[TargetDatabase] = None


def set_database(csv_path: str) -> None:
    """设置/重载全局数据库。"""
    global _DB
    db = TargetDatabase(csv_path)
    db.load()
    _DB = db


def _ensure_db(csv_path: Optional[str] = None) -> TargetDatabase:
    global _DB
    if _DB is not None and _DB.is_loaded():
        return _DB

    # 优先使用显式传入的路径，其次默认文件路径
    path = csv_path or DEFAULT_CSV_PATH
    db = TargetDatabase(path)
    db.load()
    _DB = db
    return db


def _compute_query_fps(smiles: str):
    if any(x is None for x in (Chem, AllChem, MACCSkeys, DataStructs)):
        raise RuntimeError("RDKit 未正确安装或导入失败。")
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            raise ValueError("输入SMILES无法被RDKit解析。")
        morgan_fp = AllChem.GetMorganFingerprintAsBitVect(mol, radius=2, nBits=2048)
        maccs_fp = MACCSkeys.GenMACCSKeys(mol)
        return morgan_fp, maccs_fp
    except Exception as e:
        raise RuntimeError(f"RDKit 生成指纹失败: {e}")


def predict_targets(smiles: str, threshold: float = 0.6, top_k: int = 10,
                    csv_path: Optional[str] = None) -> pd.DataFrame:
    """
    基于 2D Tanimoto 相似度 + Morgan + MACCS 的反向寻靶预测。

    最终相似度 = (Morgan 相似度 + MACCS 相似度) / 2

    返回: pandas.DataFrame，列包含 [target_name, canonical_smiles, morgan_sim, maccs_sim, final_sim]
    同时将结果打印到命令行。
    """
    try:
        db = _ensure_db(csv_path)
        if not db.entries:
            return pd.DataFrame(columns=["target_name", "canonical_smiles", "morgan_sim", "maccs_sim", "final_sim"]).head(0)

        q_morgan, q_maccs = _compute_query_fps(smiles)

        results = []
        for entry in db.entries:
            try:
                # 分别计算 Tanimoto 相似度
                morgan_sim = DataStructs.TanimotoSimilarity(q_morgan, entry.morgan_fp) if entry.morgan_fp is not None else 0.0
                maccs_sim = DataStructs.TanimotoSimilarity(q_maccs, entry.maccs_fp) if entry.maccs_fp is not None else 0.0
                final_sim = (morgan_sim + maccs_sim) / 2.0
                if final_sim >= threshold:
                    results.append({
                        "target_name": entry.target_name,
                        "canonical_smiles": entry.smiles,
                        "morgan_sim": morgan_sim,
                        "maccs_sim": maccs_sim,
                        "final_sim": final_sim,
                    })
            except Exception:
                # 单条失败不影响整体
                continue

        if not results:
            df = pd.DataFrame(columns=["target_name", "canonical_smiles", "morgan_sim", "maccs_sim", "final_sim"]).head(0)
            print("无满足阈值的候选。")
            return df

        out_df = pd.DataFrame(results).sort_values(by="final_sim", ascending=False).head(top_k).reset_index(drop=True)
        # 命令行打印
        print("预测结果（前{}条，阈值>= {}）:".format(min(top_k, len(out_df)), threshold))
        with pd.option_context('display.max_rows', None, 'display.max_columns', None, 'display.width', 120):
            print(out_df)
        return out_df

    except Exception as e:
        # 顶层异常处理：确保不会因 RDKit/IO 问题直接崩溃
        print("[ERROR] 预测失败: ", str(e))
        traceback.print_exc()
        return pd.DataFrame(columns=["target_name", "canonical_smiles", "morgan_sim", "maccs_sim", "final_sim"]).head(0)


def _example_main():
    """示例主函数，可直接运行。

    运行示例：
        python target_reverse.py --csv path/to/your_dataset.csv --smiles "CCO"

    若未指定 --csv，则尝试使用同目录下的 targets_dataset.csv。
    """
    import argparse

    parser = argparse.ArgumentParser(description="Reverse Target Prediction with Morgan + MACCS Tanimoto")
    parser.add_argument("--csv", type=str, default=None, help="包含 canonical_smiles,target_name 的CSV路径")
    parser.add_argument("--smiles", type=str, required=True, help="待预测分子的SMILES")
    parser.add_argument("--threshold", type=float, default=0.6, help="最小相似度阈值")
    parser.add_argument("--top_k", type=int, default=10, help="返回前K条")

    args = parser.parse_args()

    # 尝试加载数据库
    try:
        predict_targets(
            smiles=args.smiles,
            threshold=args.threshold,
            top_k=args.top_k,
            csv_path=args.csv or DEFAULT_CSV_PATH,
        )
    except Exception as e:
        print("[FATAL] 示例运行失败:", e)
        sys.exit(1)


if __name__ == "__main__":
    _example_main()
