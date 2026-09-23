"""Small RAG projections: keep source metadata separate from display properties."""
import json
import math


def _display_value(value):
    if value is None or isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return round(float(value), 2) if isinstance(value, (int, float)) else str(value)


def rag_info_molecule(record):
    result = {
        "smiles": record.get("SMILES", ""),
        "similarity": round(float(record.get("similarity_score", 0)), 3),
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
        parts.append(f"{position}. SMILES: {record.get('SMILES', 'Unknown')} "
                     f"(similarity: {record.get('similarity_score', 0):.3f})")
        for key, value in record.items():
            if key not in {"SMILES", "similarity_score"} and _display_value(value) is not None:
                rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, tuple)) else value
                parts.append(f"   {key}: {rendered}")
    return "\n".join(parts)
