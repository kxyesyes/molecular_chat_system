"""Persistence helpers for molecular design results."""

from __future__ import annotations

import datetime
import os
import threading
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from .chemistry import canonicalize_smiles


class DesignStorage:
    def __init__(self, save_dir: str | Path):
        self.save_dir = Path(save_dir)
        self._lock = threading.Lock()

    def save_molecule(self, smiles: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        canonical_smiles = canonicalize_smiles(smiles)
        self.save_dir.mkdir(parents=True, exist_ok=True)
        file_path = self.save_dir / "saved_molecules.csv"

        row = {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "smiles": canonical_smiles,
            **{f"prop_{key}": value for key, value in (properties or {}).items()},
        }

        with self._lock:
            df = pd.DataFrame([row])
            df.to_csv(file_path, mode="a" if file_path.exists() else "w", header=not file_path.exists(), index=False)

        return {"success": True, "message": f"分子已保存到 {os.path.basename(file_path)}", "smiles": canonical_smiles}

    def export_history_csv(self, history: List[Dict[str, Any]]) -> tuple[str, str]:
        if not history:
            raise ValueError("历史记录为空，无法导出")

        rows = []
        for item in history:
            row = {
                "step": item.get("step", 0),
                "smiles": item.get("smi", ""),
            }
            for key, value in (item.get("props", {}) or {}).items():
                row[f"prop_{key}"] = value
            rows.append(row)

        csv_content = pd.DataFrame(rows).to_csv(index=False)
        filename = f"molecular_design_history_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return filename, csv_content
