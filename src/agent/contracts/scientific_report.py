"""Bounded detached ScientificReport@1. A display DTO, never scientific authority."""
import json
import math
import re
from hashlib import sha256

from .scientific_references import reference_json

DESCRIPTORS = ("molecular_weight", "logp", "tpsa", "qed")
POSITIVE = {"available", "partial"}
STATES = POSITIVE | {"not_provided", "unavailable", "invalid", "ambiguous"}
REASONS = {"none", "missing_source", "source_unavailable", "source_failed", "source_mismatch",
    "input_unverifiable", "alignment_missing", "row_missing", "invalid_value", "ambiguous_source",
    "stale_source", "candidate_not_displayed", "partial_source", "unsupported_binding"}


def _require(condition):
    if not condition:
        raise ValueError("invalid ScientificReport@1")


def _keys(value, keys):
    _require(type(value) is dict and set(value) == set(keys.split()))


def _text(value, limit=128, nullable=False):
    _require((nullable and value is None) or
             (type(value) is str and 0 < len(value) <= limit and bool(value.strip())))


def _digest(value, nullable=False):
    _require((nullable and value is None) or
             (type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None))


def _count(value, nullable=False):
    _require((nullable and value is None) or
             (type(value) is int and 0 <= value <= 9007199254740991))


def descriptor_values(values):
    _keys(values, " ".join(DESCRIPTORS))
    for key, value in values.items():
        if value is None:
            continue
        _require(type(value) in (float, int) and math.isfinite(value))
        _require(key != "molecular_weight" or value > 0)
        _require(key != "tpsa" or value >= 0)
        _require(key != "qed" or 0 <= value <= 1)


def _missing(value):
    _require(type(value) is list and len(value) <= 2
             and all(v in ("admet", "activity") for v in value) and len(set(value)) == len(value))


def _unit(value, nullable=False):
    _require((nullable and value is None) or
        (type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1))


def validate_report(value):
    try:
        return _validate_report(value)
    except (TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise ValueError("invalid ScientificReport@1") from None


def _validate_report(value):
    # Existing preflight rejects hooks/cycles/invalid Unicode before any traversal.
    encoded = reference_json(value)
    _require(len(encoded.encode("utf-8")) <= 128 * 1024)
    nodes = 0
    def bound(item, depth=0):
        nonlocal nodes
        nodes += 1
        _require(depth <= 12 and nodes <= 8192)
        if type(item) is dict:
            for k, v in item.items():
                bound(k, depth + 1)
                bound(v, depth + 1)
        elif type(item) is list:
            for v in item:
                bound(v, depth + 1)
    bound(value)
    _keys(value, "type schema_version trace_id source_version projection_id run_status target sources "
          "generations collections property_rows steps ranking warnings omitted")
    _require(value["type"] == "scientific_report" and value["schema_version"] == "1")
    _require(value["run_status"] in {"completed", "partial"})
    _text(value["trace_id"])
    _digest(value["source_version"])
    _digest(value["projection_id"])
    _require(value["projection_id"] == sha256(reference_json({k: v for k, v in value.items()
        if k != "projection_id"}).encode("utf-8")).hexdigest())
    _keys(value["target"], "label origin")
    _text(value["target"]["label"], nullable=True)
    _require(value["target"]["origin"] == ("not_provided" if value["target"]["label"] is None else "workflow_plan"))
    for name, maximum in (("sources", 24), ("generations", 8), ("collections", 8),
                          ("property_rows", 32), ("steps", 32), ("warnings", 32)):
        _require(type(value[name]) is list and len(value[name]) <= maximum)
    sources = {}
    for s in value["sources"]:
        _keys(s, "observation_id step_id tool_name tool_version evidence_id status input_digest "
              "data_digest observation_digest output_digest_origin model_name model_version method backend_version")
        for k in ("observation_id", "step_id", "tool_name", "tool_version", "evidence_id"):
            _text(s[k])
        for k in ("input_digest", "data_digest", "observation_digest"):
            _digest(s[k])
        for k in ("model_name", "model_version", "method", "backend_version"):
            _text(s[k], nullable=True)
        _require(s["status"] in {"succeeded", "partial"})
        _require(s["output_digest_origin"] in {"recorded", "checkpoint_snapshot"})
        _require(s["observation_id"] not in sources)
        sources[s["observation_id"]] = s
    keys, presentations = set(), set()
    for c in value["collections"]:
        _keys(c, "reference generator_observation_id")
        r = c["reference"]
        _keys(r, "trace_id presentation_id revision ordered_keys")
        _require(r["trace_id"] == value["trace_id"])
        _text(r["presentation_id"])
        _digest(r["revision"])
        _require(r["presentation_id"] not in presentations)
        presentations.add(r["presentation_id"])
        oid = c["generator_observation_id"]
        _require(oid in sources and sources[oid]["tool_name"] == "llm_molecular_generator")
        _require(type(r["ordered_keys"]) is list and 1 <= len(r["ordered_keys"]) <= 32)
        for pair in r["ordered_keys"]:
            _require(type(pair) is list and len(pair) == 2 and pair[0] == oid)
            _text(pair[1])
            _require(tuple(pair) not in keys)
            keys.add(tuple(pair))
    generated = set()
    for g in value["generations"]:
        counts = "requested_count valid_count unique_count invalid_count duplicate_count displayed_count"
        _keys(g, "source_observation_id status " + counts)
        _require(g["status"] in {"succeeded", "partial", "unknown"})
        if g["status"] == "unknown":
            _require(g["source_observation_id"] is None and all(g[k] is None for k in counts.split()))
        else:
            oid = g["source_observation_id"]
            _require(oid in sources and sources[oid]["tool_name"] == "llm_molecular_generator"
                     and sources[oid]["status"] == g["status"] and oid not in generated)
            generated.add(oid)
            for k in counts.split():
                _count(g[k])
            _require(g["displayed_count"] == sum(k[0] == oid for k in keys)
                     and g["displayed_count"] <= g["unique_count"] <= g["valid_count"])
    rows = set()
    for r in value["property_rows"]:
        _keys(r, "generator_observation_id candidate_id canonical_smiles source_observation_id "
              "source_row_index row_digest state reason_code values")
        key = (r["generator_observation_id"], r["candidate_id"])
        _require(key in keys and key not in rows)
        rows.add(key)
        _text(r["canonical_smiles"], 8192)
        _require(r["state"] in STATES and r["reason_code"] in REASONS)
        descriptor_values(r["values"])
        if r["state"] in POSITIVE:
            s = sources.get(r["source_observation_id"])
            _require(s is not None and s["tool_name"] == "property_calculator")
            _count(r["source_row_index"])
            _digest(r["row_digest"])
            _require(all(v is not None for v in r["values"].values()))
            if r["state"] == "available":
                _require(s["status"] == "succeeded")
        else:
            _require(all(v is None for v in r["values"].values()))
            _require(all(r[k] is None for k in ("source_observation_id", "source_row_index", "row_digest")))
    for s in value["steps"]:
        _keys(s, "step_id tool_name status source_observation_id reason_code message")
        _text(s["step_id"])
        _text(s["tool_name"])
        _text(s["message"], 256)
        _require(s["status"] in {"succeeded", "partial", "failed", "rejected", "cancelled", "skipped", "unknown"})
        if s["status"] in {"succeeded", "partial"}:
            source = sources.get(s["source_observation_id"])
            _require(source is not None and s["reason_code"] == "none"
                     and all(s[k] == source[k] for k in ("step_id", "tool_name", "status")))
        else:
            _require(s["source_observation_id"] is None)
            allowed_reasons = {
                "failed": {"source_failed"}, "rejected": {"source_failed"},
                "cancelled": {"source_failed"}, "skipped": {"source_unavailable"},
                "unknown": {"source_mismatch", "source_unavailable", "source_failed"},
            }
            _require(s["reason_code"] in allowed_reasons[s["status"]])
    rank = value["ranking"]
    _keys(rank, "state reason_code source_observation_id generator_observation_id requested_top_n "
          "ranked_candidate_count top_candidates unrankable_candidates")
    _require(rank["state"] in STATES and rank["reason_code"] in REASONS)
    for k in ("top_candidates", "unrankable_candidates"):
        _require(type(rank[k]) is list and len(rank[k]) <= 32)
    if rank["state"] not in POSITIVE:
        _require(not rank["top_candidates"] and not rank["unrankable_candidates"])
        _require(all(rank[k] is None for k in ("source_observation_id", "generator_observation_id",
                                              "requested_top_n", "ranked_candidate_count")))
    else:
        s = sources.get(rank["source_observation_id"])
        _require(s is not None and s["tool_name"] == "candidate_ranker")
        _count(rank["requested_top_n"])
        _count(rank["ranked_candidate_count"])
        _require(rank["requested_top_n"] > 0 and len(rank["top_candidates"]) <=
                 min(rank["requested_top_n"], rank["ranked_candidate_count"]))
        ranked = set()
        for row in rank["top_candidates"] + rank["unrankable_candidates"]:
            key = (rank["generator_observation_id"], row["candidate_id"])
            _require(key in keys and key not in ranked)
            ranked.add(key)
            _text(row["canonical_smiles"], 8192)
        for row in rank["unrankable_candidates"]:
            _keys(row, "candidate_id canonical_smiles reason")
            _text(row["reason"], 256)
        for row in rank["top_candidates"]:
            _keys(row, "candidate_id canonical_smiles score missing_evidence ranking_evidence")
            _unit(row["score"])
            _missing(row["missing_evidence"])
            ev = row["ranking_evidence"]
            _keys(ev, "property_score admet_score activity_score weights_used missing_evidence")
            _unit(ev["property_score"])
            _unit(ev["admet_score"], True)
            _unit(ev["activity_score"], True)
            _keys(ev["weights_used"], "properties admet activity")
            for k, v in ev["weights_used"].items():
                _unit(v, k != "properties")
            _missing(ev["missing_evidence"])
            _require(ev["missing_evidence"] == row["missing_evidence"])
    for w in value["warnings"]:
        _text(w, 256)
    _keys(value["omitted"], "generations steps property_rows ranking_rows warnings")
    for c in value["omitted"].values():
        _count(c)
    return json.loads(encoded)
