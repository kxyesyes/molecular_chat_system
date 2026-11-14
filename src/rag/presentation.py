"""Small RAG projections: keep source metadata separate from display properties."""

import json
import math


def _display_value(value):
    if value is None or isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return value if isinstance(value, (int, float)) else str(value)


def _retrieval_score(record):
    score = record.get("similarity_score")
    if score is not None and (type(score) not in (int, float) or not math.isfinite(score)):
        raise ValueError("Invalid retrieval score")
    return score


def rag_info_molecule(record):
    result = {
        "smiles": record.get("SMILES", ""),
        "similarity": _retrieval_score(record),
        "properties": {
            key: displayed for key, value in record.items()
            if key not in {"SMILES", "similarity_score", "source_index", "provenance"}
            and (displayed := _display_value(value)) is not None
        },
    }
    for key in ("source_index", "provenance"):
        if key in record:
            result[key] = record[key]
    return result


def format_rag_context(molecules):
    if not molecules:
        return ""
    parts = ["Relevant molecular data found:"]
    for position, record in enumerate(molecules, 1):
        score = _retrieval_score(record)
        parts.append(f"{position}. SMILES: {record.get('SMILES', 'Unknown')} "
                     f"(similarity: {score if score is not None else 'not provided'})")
        for key, value in record.items():
            if key not in {"SMILES", "similarity_score"} and _display_value(value) is not None:
                rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, tuple)) else value
                parts.append(f"   {key}: {rendered}")
    return "\n".join(parts)


__all__ = ["format_rag_context", "rag_info_molecule"]
