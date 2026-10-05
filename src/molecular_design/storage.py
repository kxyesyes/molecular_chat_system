"""Fixed-schema persistence helpers for molecular design results."""

from __future__ import annotations

import datetime
import math
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Mapping

import pandas as pd

from .chemistry import calculate_properties, canonicalize_smiles


PROPERTY_KEYS = (
    "mw",
    "logp",
    "qed",
    "tpsa",
    "hbd",
    "hba",
    "rotbonds",
    "sa_score",
    "morgan_similarity",
)
HISTORY_COLUMNS = ["step", "smiles", *[f"prop_{key}" for key in PROPERTY_KEYS]]
MOLECULE_COLUMNS = ["timestamp", "smiles", *[f"prop_{key}" for key in PROPERTY_KEYS]]


def _fixed_property_values(properties: Mapping[str, Any] | None) -> Dict[str, Any]:
    properties = properties or {}
    return {f"prop_{key}": properties.get(key) for key in PROPERTY_KEYS}


def _canonical_history_row(item: Mapping[str, Any]) -> Dict[str, Any]:
    smiles = str(item.get("smi") or item.get("smiles") or "").strip()
    if not smiles:
        raise ValueError("历史记录缺少 SMILES")
    canonical = canonicalize_smiles(smiles)
    return {
        "step": item.get("step", 0),
        "smiles": canonical,
        **_fixed_property_values(item.get("props") or {}),
    }


class DesignStorage:
    def __init__(self, save_dir: str | Path):
        self.save_dir = Path(save_dir)
        self._lock = threading.Lock()

    def save_molecule(self, smiles: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        canonical_smiles = canonicalize_smiles(smiles)
        calculated = calculate_properties(canonical_smiles)["properties"]
        for key, supplied in (properties or {}).items():
            if key not in calculated or supplied in (None, "") or calculated.get(key) is None:
                continue
            try:
                supplied_value = float(supplied)
                calculated_value = float(calculated[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"属性 {key} 不是可验证的数值，未保存") from exc
            if not math.isfinite(supplied_value) or not math.isfinite(calculated_value):
                raise ValueError(f"属性 {key} 不是有限数值，未保存")
            if abs(supplied_value - calculated_value) > 1e-4:
                raise ValueError(f"属性 {key} 与当前 SMILES 不一致，未保存")

        self.save_dir.mkdir(parents=True, exist_ok=True)
        file_path = self.save_dir / "saved_molecules.csv"
        row = {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "smiles": canonical_smiles,
            **_fixed_property_values(calculated),
        }

        with self._lock:
            existing = pd.read_csv(file_path) if file_path.exists() else pd.DataFrame(columns=MOLECULE_COLUMNS)
            existing = existing.reindex(columns=MOLECULE_COLUMNS)
            combined = pd.concat([existing, pd.DataFrame([row], columns=MOLECULE_COLUMNS)], ignore_index=True)
            temp_path = file_path.with_suffix(".csv.tmp")
            combined.to_csv(temp_path, index=False)
            os.replace(temp_path, file_path)

        return {"success": True, "message": f"分子已保存到 {file_path.name}", "smiles": canonical_smiles}

    def export_history_csv(self, history: List[Dict[str, Any]]) -> tuple[str, str]:
        if not history:
            raise ValueError("历史记录为空，无法导出")
        rows = [_canonical_history_row(item) for item in history]
        csv_content = pd.DataFrame(rows, columns=HISTORY_COLUMNS).to_csv(index=False)
        filename = f"molecular_design_history_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return filename, csv_content
