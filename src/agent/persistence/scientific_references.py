"""Owner-bound scientific views in existing run metadata; no browser data authority.

Public store methods are for trusted server callers. A selection is a checkpoint
identity, never a supplied molecule. The eventual Web service must determine the
actual display manifest and pass the authenticated session from request scope.
"""
from contextlib import closing
from hashlib import sha256
import json
import time

from src.agent.contracts import CandidateSet, ObservationStatus, ToolProvenance, ToolResult
from src.agent.contracts.scientific_references import ScientificPresentation, reference_json
from src.agent.evidence.ledger import EvidenceLedger
from src.agent.validators.result_validator import AgentResultValidator


FIELD = "scientific_presentations"
_ACCEPTED = {"succeeded", "partial"}
_MAX_METADATA_BYTES = 2 * 1024 * 1024


def _identity(*values):
    return all(type(value) is str and 0 < len(value) <= 128 and value.strip()
               for value in values)


def _namespace(metadata):
    values = metadata.get(FIELD, [])
    _namespace_budget(values)
    if type(values) is not list or len(values) > 8:
        raise ValueError("invalid scientific presentation namespace")
    ids = set()
    for item in values:
        if (type(item) is not dict or set(item) != {"presentation", "confirmed"}
                or type(item["confirmed"]) is not bool):
            raise ValueError("invalid scientific presentation entry")
        view = ScientificPresentation.from_dict(item["presentation"]).to_dict()
        if view["presentation_id"] in ids:
            raise ValueError("duplicate scientific presentation identity")
        ids.add(view["presentation_id"])
    return values


def _namespace_budget(values):
    reference_json(values)
    # The legacy metadata writer uses spaced JSON. Bound that actual storage
    # representation too, not only the smaller canonical digest representation.
    if len(json.dumps(values, ensure_ascii=False, sort_keys=True).encode("utf-8")) > 512 * 1024:
        raise ValueError("scientific presentation namespace exceeds byte budget")


def _source(connection, trace_id, session_id):
    # Bound persisted input before materializing it in Python. Larger historical
    # runs remain intact, but are not eligible for this small reference protocol.
    row = connection.execute(
        """SELECT * FROM agent_runs WHERE trace_id=? AND session_id=?
           AND length(CAST(metadata_json AS BLOB)) <= ?
           AND length(CAST(COALESCE(query, '') AS BLOB)) <= 524288""",
        (trace_id, session_id, _MAX_METADATA_BYTES),
    ).fetchone()
    if row is None or row["status"] not in {"completed", *_ACCEPTED}:
        raise ValueError("scientific source unavailable")
    size = connection.execute(
        """SELECT count(*), COALESCE(sum(length(CAST(COALESCE(output_json, '') AS BLOB))
                    + length(CAST(COALESCE(error_json, '') AS BLOB))
                    + length(CAST(metadata_json AS BLOB))), 0)
           FROM agent_checkpoints WHERE trace_id=?""", (trace_id,),
    ).fetchone()
    if size[0] > 256 or size[1] > 4 * 1024 * 1024:
        raise ValueError("scientific source exceeds recovery budget")
    checkpoints = [dict(item) for item in connection.execute(
        "SELECT * FROM agent_checkpoints WHERE trace_id=? ORDER BY created_at, rowid",
        (trace_id,),
    )]
    # Metadata timestamps are intentionally excluded: unrelated metadata writes
    # are not new scientific observations. Source identity and all checkpoints are.
    source = {key: row[key] for key in (
        "trace_id", "session_id", "user_id", "status", "workflow_version",
        "query", "skill_name", "idempotency_key", "created_at")}
    digest = sha256(json.dumps([source, checkpoints], ensure_ascii=False,
                              sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()
    metadata = json.loads(row["metadata_json"])
    if type(metadata) is not dict:
        raise ValueError("invalid run metadata")
    views = _namespace(metadata)
    latest = {item["step_id"]: item for item in checkpoints}
    return row, metadata, views, latest, digest


def _candidates(checkpoint, trace_id):
    if (checkpoint["status"] not in _ACCEPTED
            or checkpoint["tool_name"] != "llm_molecular_generator"
            or checkpoint["error_json"] is not None):
        raise ValueError("candidate checkpoint unavailable")
    raw = checkpoint["output_json"]
    if type(raw) is not str or len(raw.encode("utf-8")) > 512 * 1024:
        raise ValueError("candidate checkpoint exceeds budget")
    value = json.loads(raw)
    reference_json(value)
    if (type(value) is not dict
            or value.get("success") is not True or value.get("status") != checkpoint["status"]
            or value.get("error") is not None
            or type(value.get("quality")) is not dict
            or value["quality"].get("output_contract") != "CandidateSet@1"
            or type(value.get("warnings")) is not list
            or any(type(warning) is not str for warning in value["warnings"])):
        raise ValueError("candidate checkpoint is not accepted")
    provenance = ToolProvenance.from_dict(value["provenance"])
    if (provenance.demo_mode or provenance.fallback_used
            or provenance.tool_name != checkpoint["tool_name"]
            or provenance.tool_version != checkpoint["tool_version"]
            or not _identity(checkpoint["input_hash"])
            or provenance.input_digest != checkpoint["input_hash"]
            or (provenance.output_digest is not None
                and provenance.output_digest != EvidenceLedger.output_digest(value["data"]))):
        raise ValueError("candidate provenance mismatch")
    # Reuse the scientific validator, including actual RDKit revalidation. No
    # model invocation. An unavailable validator cannot publish a usable view.
    result = ToolResult(
        tool_name=checkpoint["tool_name"], success=True, message=value.get("message", ""),
        data=value["data"], status=ObservationStatus(value["status"]),
        formatted=value.get("formatted", ""), warnings=value["warnings"],
        evidence=value["evidence"], quality=value["quality"], provenance=provenance,
    )
    # Candidate generation has no required disk artifacts in CandidateSet@1.
    # Preserve optional artifact references in the presentation evidence below.
    from src.agent.contracts import WorkflowArtifact
    result.artifacts = [WorkflowArtifact.from_dict(item) for item in value["artifacts"]]
    expected_evidence = result.quality.get("evidence_id")
    if EvidenceLedger(trace_id).register_tool_result(
            checkpoint["step_id"], checkpoint["input_hash"], result) != expected_evidence:
        raise ValueError("candidate evidence identity mismatch")
    before_data = reference_json(result.data)
    result = AgentResultValidator().validate_tool_result(result, trusted_checkpoint=True)
    if (not result.success or result.status.value != checkpoint["status"]
            or reference_json(result.data) != before_data):
        raise ValueError("candidate scientific validation failed")
    candidates = CandidateSet.from_dict(result.data)
    return candidates, result, expected_evidence


def sources(store, trace_id, *, session_id):
    """Owned consistent snapshot; never expose get_run to the browser."""
    if not _identity(trace_id, session_id):
        return None
    with closing(store._connect()) as connection, connection:
        connection.execute("BEGIN")
        try:
            row, _, _, latest, version = _source(connection, trace_id, session_id)
            output = []
            for checkpoint in latest.values():
                if checkpoint["tool_name"] != "llm_molecular_generator":
                    continue
                try:
                    if checkpoint["workflow_version"] != row["workflow_version"]:
                        continue
                    _candidates(checkpoint, trace_id)
                except (ValueError, TypeError, KeyError, RecursionError):
                    # A failed independent branch is not evidence for that
                    # branch, but must not erase another accepted observation.
                    continue
                output.append({"observation_id": checkpoint["id"],
                               "source_version": version,
                               "step_id": checkpoint["step_id"],
                               "observation": {**json.loads(checkpoint["output_json"]),
                                               "tool_name": checkpoint["tool_name"]}})
            return output
        except (ValueError, TypeError, KeyError, RecursionError):
            return None


def publish(store, trace_id, *, session_id, selections, target=None):
    if not _identity(trace_id, session_id):
        return None
    try:
        selections = json.loads(reference_json(selections))
        if (type(selections) is not list or not 1 <= len(selections) <= 32
                or any(type(item) is not dict or set(item) != {"observation_id", "candidate_id"}
                       or not _identity(item["observation_id"], item["candidate_id"])
                       for item in selections)):
            return None
        reference_json(target)
    except (ValueError, TypeError, RecursionError):
        return None
    with store._lock, closing(store._connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            row, metadata, views, latest, version = _source(connection, trace_id, session_id)
            by_id = {item["id"]: item for item in latest.values()}
            decoded, ordered, evidence, warnings = {}, [], [], []
            for selection in selections:
                oid = selection["observation_id"]
                if oid not in decoded:
                    checkpoint = by_id[oid]
                    if checkpoint["workflow_version"] != row["workflow_version"]:
                        raise ValueError("candidate workflow version mismatch")
                    candidates, result, eid = _candidates(checkpoint, trace_id)
                    decoded[oid] = {c.candidate_id: c.to_dict() for c in candidates.candidates}
                    warnings.extend(result.warnings)
                    evidence.append({"observation_id": oid, "evidence_id": eid,
                                     "status": result.status.value,
                                     "provenance": result.provenance.to_dict(),
                                     "evidence": result.evidence,
                                     "artifacts": [a.to_dict() for a in result.artifacts]})
                ordered.append({"observation_id": oid,
                                "candidate": decoded[oid][selection["candidate_id"]]})
            now = time.time()
            if any(item["presentation"]["created_at"] > now for item in views):
                return None
            views = [item for item in views if item["presentation"]["expires_at"] > now]
            for item in views:
                existing = item["presentation"]
                if (existing["source_version"] == version
                        and existing["ordered_candidates"] == ordered and existing["target"] == target):
                    return existing
            if len(views) >= 8:
                return None
            presentation = ScientificPresentation.create(
                source_trace_id=trace_id, source_version=version,
                source_status="partial" if row["status"] == "partial" else "succeeded",
                ordered_candidates=ordered, target=target, evidence=evidence,
                warnings=list(dict.fromkeys(warnings)), created_at=now,
            ).to_dict()
            views.append({"presentation": presentation, "confirmed": False})
            _namespace_budget(views)
            metadata[FIELD] = views
            encoded_metadata = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
            if len(encoded_metadata.encode("utf-8")) > _MAX_METADATA_BYTES:
                return None
        except (ValueError, TypeError, KeyError, RecursionError):
            return None
        connection.execute("UPDATE agent_runs SET metadata_json=?, updated_at=? WHERE trace_id=?",
                           (encoded_metadata, now, trace_id))
    return presentation


def _locate(connection, trace_id, session_id, presentation_id, revision):
    _, metadata, views, _, version = _source(connection, trace_id, session_id)
    now = time.time()
    for item in views:
        view = item["presentation"]
        if (view["presentation_id"] == presentation_id and view["revision"] == revision
                and view["source_trace_id"] == trace_id and view["source_version"] == version
                and view["created_at"] <= now < view["expires_at"]):
            return metadata, item
    raise ValueError("scientific presentation unavailable")


def confirm(store, trace_id, *, session_id, presentation_id, revision, ordered_keys):
    if not _identity(trace_id, session_id, presentation_id, revision):
        return False
    try:
        ordered_keys = json.loads(reference_json(ordered_keys))
    except (ValueError, TypeError, RecursionError):
        return False
    with store._lock, closing(store._connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            metadata, item = _locate(connection, trace_id, session_id, presentation_id, revision)
            expected = [[c["observation_id"], c["candidate"]["candidate_id"]]
                        for c in item["presentation"]["ordered_candidates"]]
            if ordered_keys != expected:
                return False
            if item["confirmed"]:
                return True
            item["confirmed"] = True
        except (ValueError, TypeError, KeyError, RecursionError):
            return False
        connection.execute("UPDATE agent_runs SET metadata_json=?, updated_at=? WHERE trace_id=?",
                           (json.dumps(metadata, ensure_ascii=False, sort_keys=True), time.time(), trace_id))
    return True


def get(store, trace_id, *, session_id, presentation_id, revision):
    if not _identity(trace_id, session_id, presentation_id, revision):
        return None
    with closing(store._connect()) as connection, connection:
        connection.execute("BEGIN")  # One consistent source/metadata snapshot.
        try:
            _, item = _locate(connection, trace_id, session_id, presentation_id, revision)
            result = item["presentation"] if item["confirmed"] else None
        except (ValueError, TypeError, KeyError, RecursionError):
            return None
    return result
