"""Strict, deterministic batch input parsing for reverse-target routes."""

from __future__ import annotations

import csv
import io


def parse_batch_rows(filename: str, text: str, *, max_rows: int = 100) -> list[dict]:
    name = str(filename or "").lower()
    if name.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(text))
        fields = reader.fieldnames or []
        smiles_column = next(
            (field for field in fields if field.strip().casefold() in {"smiles", "smile", "canonical_smiles"}),
            None,
        )
        if smiles_column is None:
            raise ValueError("CSV file must contain a SMILES column (smiles, smile, or canonical_smiles)")
        rows = [{"row_index": index, "smiles": (row.get(smiles_column) or "").strip()} for index, row in enumerate(reader)]
    elif name.endswith((".txt", ".tsv")):
        rows = [{"row_index": index, "smiles": line.strip()} for index, line in enumerate(text.splitlines())]
    else:
        raise ValueError("仅支持 CSV 或文本文件")
    if len(rows) > max_rows:
        raise ValueError(f"批量行数不能超过 {max_rows}")
    return rows
