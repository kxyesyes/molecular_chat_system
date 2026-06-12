"""Fragment library loading, filtering, and recommendation helpers."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd


LEGACY_TAG_TO_LABEL = {
    "lipophilic": "label_lipophilic",
    "lipo": "label_lipophilic",
    "hydrophilic": "label_hydrophilic",
    "hydro": "label_hydrophilic",
    "amphiphilic": "label_amphiphilic",
    "acid": "label_acidic",
    "acidic": "label_acidic",
    "base": "label_basic",
    "basic": "label_basic",
    "neutral": "label_neutral",
    "zwitterionic": "label_zwitterionic",
    "aromatic": "label_has_aromatic_ring",
    "arom": "label_has_aromatic_ring",
    "ring": "label_has_aromatic_ring",
    "halogen": "label_has_halogen",
    "halo": "label_has_halogen",
    "heterocycle": "label_has_heterocycle",
    "amide": "label_has_amide",
    "ester": "label_has_ester",
    "bioisostere": "label_bioisostere",
}

COMMAND_KEYWORD_LABELS = {
    ("脂溶", "亲脂", "logp", "lipo"): "label_lipophilic",
    ("水溶", "亲水", "极性", "tpsa", "降低logp"): "label_hydrophilic",
    ("碱性", "胺", "amine"): "label_basic",
    ("酸性", "羧酸", "cooh"): "label_acidic",
    ("芳香", "aromatic", "苯环"): "label_has_aromatic_ring",
    ("卤素", "halogen", "氟", "氯"): "label_has_halogen",
    ("杂环", "heterocycle"): "label_has_heterocycle",
}


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return None
    if isinstance(value, np.generic):
        return value.item()
    return value


def _row_to_dict(row: pd.Series) -> Dict[str, Any]:
    return {col: _json_safe(row[col]) for col in row.index}


def parse_legacy_tags(tags: str, available_columns: Iterable[str]) -> List[str]:
    available = set(available_columns)
    labels: List[str] = []
    for raw_tag in (tags or "").split(","):
        tag = raw_tag.strip().lower()
        if not tag:
            continue
        label = LEGACY_TAG_TO_LABEL.get(tag, raw_tag.strip())
        if label in available and label not in labels:
            labels.append(label)
    return labels


def infer_label_filters(command: str, available_columns: Iterable[str]) -> List[str]:
    available = set(available_columns)
    command_lower = (command or "").lower()
    labels: List[str] = []
    for keywords, label in COMMAND_KEYWORD_LABELS.items():
        if label in available and any(keyword in command_lower for keyword in keywords):
            labels.append(label)
    return labels


class FragmentRepository:
    """Cached reader for the local fragment CSV."""

    def __init__(self, csv_path: str | Path):
        self.csv_path = Path(csv_path)
        self._df: Optional[pd.DataFrame] = None
        self._lock = threading.Lock()

    def load(self) -> pd.DataFrame:
        if self._df is not None:
            return self._df
        with self._lock:
            if self._df is not None:
                return self._df
            if not self.csv_path.exists():
                self._df = pd.DataFrame()
            else:
                self._df = pd.read_csv(self.csv_path)
            return self._df

    def query(
        self,
        page: int = 1,
        page_size: int = 20,
        search: str = "",
        q: str = "",
        tags: str = "",
        label_filters: Optional[Dict[str, Optional[int]]] = None,
    ) -> Dict[str, Any]:
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 20), 1), 100)

        df = self.load().copy()
        if df.empty:
            return {"success": True, "fragments": [], "total": 0, "page": page, "page_size": page_size}

        effective_search = search or q
        if effective_search and "fragment_smiles" in df.columns:
            df = df[df["fragment_smiles"].str.contains(effective_search, case=False, na=False)]

        for label in parse_legacy_tags(tags, df.columns):
            df = df[df[label] == 1]

        for col, value in (label_filters or {}).items():
            if value is not None and col in df.columns:
                df = df[df[col] == value]

        total = len(df)
        start = (page - 1) * page_size
        page_df = df.iloc[start:start + page_size]
        return {
            "success": True,
            "fragments": [_row_to_dict(row) for _, row in page_df.iterrows()],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def recommend_for_command(self, command: str, limit: int = 6) -> List[Dict[str, Any]]:
        df = self.load()
        if df.empty:
            return []

        sub = df.copy()
        for label in infer_label_filters(command, df.columns):
            sub = sub[sub[label] == 1]

        if sub.empty:
            sub = df

        if "frequency" in sub.columns:
            sub = sub.sort_values("frequency", ascending=False)
        return [_row_to_dict(row) for _, row in sub.head(limit).iterrows()]
