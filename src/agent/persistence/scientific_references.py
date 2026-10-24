"""Owner-bound scientific views in existing run metadata; no browser data authority.

Public store methods are for trusted server callers. A selection is a checkpoint
identity, never a supplied molecule. The eventual Web service must determine the
actual display manifest and pass the authenticated session from request scope.
"""
from contextlib import closing
from hashlib import sha256
import json
import re
import time

from src.agent.contracts import CandidateSet, ObservationStatus, ToolProvenance, ToolResult
from src.agent.contracts.scientific_references import ScientificPresentation, reference_json
from src.agent.contracts.target_request import analyze_target_request
from src.agent.evidence.ledger import EvidenceLedger
from src.agent.validators.result_validator import AgentResultValidator


FIELD = "scientific_presentations"
_ACCEPTED = {"succeeded", "partial"}
_MAX_METADATA_BYTES = 2 * 1024 * 1024
_WEB_PROFILE = "medchat-web-v1"


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
    metadata = json.loads(row["metadata_json"])
    if type(metadata) is not dict:
        raise ValueError("invalid run metadata")
    identity = [source, checkpoints]
    requirements = metadata.get("task_requirements")
    if (type(requirements) is dict and requirements.get("profile") == _WEB_PROFILE
            or metadata.get("binding_profile") == _WEB_PROFILE):
        # Web proof authority includes the server plan, not only checkpoints.
        # Changing/removing it must also revoke an already confirmed view.
        # Legacy sources keep their existing digest and ignore unrelated writes.
        identity.append({key: metadata[key] for key in (
            "task_requirements", "binding_profile", "decision_publication_invalidation")
            if key in metadata})
    digest = sha256(json.dumps(identity, ensure_ascii=False,
                              sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()
    views = _namespace(metadata)
    latest = {item["step_id"]: item for item in checkpoints}
    return row, metadata, views, latest, digest


def _candidate_binding_proof(checkpoint, row, run_metadata, result):
    """Restore explicit server proof authority, never opt in from tool quality.

    Terminal runs retain the accepted step metadata/record commitment rather
    than the continuation input journal. This checks that persisted authority;
    it does not replay a resolver or execute/current-check scientific sources.
    """
    from src.agent.contracts.decision import decode_protocol_json
    from src.agent.contracts.web_requirements import parse_web_requirements

    metadata = decode_protocol_json(checkpoint["metadata_json"], max_bytes=64 * 1024)
    if type(metadata) is not dict:
        raise ValueError("invalid candidate checkpoint metadata")
    raw_requirements = run_metadata.get("task_requirements")
    if not (type(raw_requirements) is dict and raw_requirements.get("profile") == _WEB_PROFILE):
        if (any(key in metadata for key in ("binding_proof", "web_step_ref", "web_plan_sha256"))
                or run_metadata.get("binding_profile") == _WEB_PROFILE):
            raise ValueError("candidate binding profile mismatch")
        return None  # Preserve legacy no-proof identities, including raw quality extras.
    if (run_metadata.get("binding_profile", _WEB_PROFILE) != _WEB_PROFILE
            or "decision_publication_invalidation" in run_metadata):
        raise ValueError("candidate binding profile unavailable")
    requirements = parse_web_requirements(raw_requirements)
    for request_key, row_key in (("trace_id", "trace_id"), ("session_id", "session_id"),
                                 ("user_id", "user_id"), ("query", "query"), ("active_skill", "skill_name")):
        if requirements.request[request_key] != row[row_key]:
            raise ValueError("candidate plan owner mismatch")
    proof = EvidenceLedger.validated_binding_proof(
        metadata.get("binding_proof"), result.quality.get("binding_proof"))
    steps = {step.name: step for step in requirements.steps}
    ref = metadata.get("web_step_ref")
    if (type(ref) is not str or ref not in steps or steps[ref].tool_name != result.tool_name
            or proof.get("profile") != _WEB_PROFILE or proof.get("step_ref") != ref
            or metadata.get("web_plan_sha256") != requirements.plan_sha256
            or proof["plan_sha256"] != requirements.plan_sha256
            or proof["requirements_sha256"] != EvidenceLedger.output_digest(requirements.model_dump(mode="json"))
            or proof["own_source"] is not None
            or [role["step_ref"] for role in proof["roles"]] != list(requirements.dependencies[ref])
            or result.quality.get("step_id") != checkpoint["step_id"]
            or result.quality.get("output_key") != checkpoint["step_id"]):
        raise ValueError("candidate binding plan mismatch")
    for key in ("binding_record_sha256", "request_input_digest", "operation_key"):
        value = metadata.get(key)
        if (type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None
                or result.quality.get(key) != value):
            raise ValueError("candidate binding commitment mismatch")
    parents = [role["evidence_id"] for role in proof["roles"]]
    if (metadata.get("input_evidence_ids") != parents or result.quality.get("input_evidence_ids") != parents
            or metadata["operation_key"] != EvidenceLedger.output_digest(
                {"action_sha256": proof["action_sha256"], "own_source": None})):
        raise ValueError("candidate binding input mismatch")
    return proof


def _candidates(checkpoint, trace_id, *, row, run_metadata):
    if (checkpoint["status"] not in _ACCEPTED
            or checkpoint["tool_name"] != "llm_molecular_generator"
            or checkpoint["error_json"] is not None):
        raise ValueError("candidate checkpoint unavailable")
    raw = checkpoint["output_json"]
    if type(raw) is not str or len(raw.encode("utf-8")) > 512 * 1024:
        raise ValueError("candidate checkpoint exceeds budget")
    value = json.loads(raw)
    reference_json(value)
    partial_success_projection = (
        type(value) is dict
        and checkpoint["status"] == "partial"
        and value.get("success") is False
    )
    if (type(value) is not dict
            or (value.get("success") is not True and not partial_success_projection)
            or value.get("status") != checkpoint["status"]
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
    # Validate the scientific payload through the normal successful-generator
    # path first. Partial is a usable observation with a false public success
    # projection, not a failed or unvalidated payload.
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
    proof = _candidate_binding_proof(checkpoint, row, run_metadata, result)
    if EvidenceLedger(trace_id).register_tool_result(
            checkpoint["step_id"], checkpoint["input_hash"], result,
            **({"binding_proof": proof} if proof is not None else {})) != expected_evidence:
        raise ValueError("candidate evidence identity mismatch")
    before_data = reference_json(result.data)
    result = AgentResultValidator().validate_tool_result(result, trusted_checkpoint=True)
    if (not result.success or result.error is not None
            or result.status.value != checkpoint["status"]
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
            row, metadata, _, latest, version = _source(connection, trace_id, session_id)
            output = []
            for checkpoint in latest.values():
                if checkpoint["tool_name"] != "llm_molecular_generator":
                    continue
                try:
                    if checkpoint["workflow_version"] != row["workflow_version"]:
                        continue
                    _candidates(checkpoint, trace_id, row=row, run_metadata=metadata)
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


def report_snapshot(store, trace_id, *, session_id, references):
    """Internal live report input, read-only and owner-bound. Never a restore DTO."""
    if not _identity(trace_id, session_id):
        return None
    try:
        references = json.loads(reference_json(references))
        if type(references) is not list or not 1 <= len(references) <= 8:
            return None
        with closing(store._connect()) as connection, connection:
            connection.execute("BEGIN")
            row, metadata, views, latest, version = _source(connection, trace_id, session_id)
            selected, ids = [], set()
            now = time.time()
            for ref in references:
                if type(ref) is not dict or set(ref) != {
                        "trace_id", "presentation_id", "revision", "ordered_keys"}:
                    return None
                matches = [item["presentation"] for item in views if
                    item["presentation"]["presentation_id"] == ref["presentation_id"]]
                if len(matches) != 1 or ref["presentation_id"] in ids:
                    return None
                view = matches[0]
                expected = {"trace_id": trace_id, "presentation_id": view["presentation_id"],
                    "revision": view["revision"], "ordered_keys": [[c["observation_id"],
                    c["candidate"]["candidate_id"]] for c in view["ordered_candidates"]]}
                if (ref != expected or view["source_trace_id"] != trace_id
                        or view["source_version"] != version
                        or not view["created_at"] <= now < view["expires_at"]):
                    return None
                ids.add(ref["presentation_id"])
                selected.append(view)
            # Internal verifier context only; these owner fields never enter the
            # closed ScientificReport DTO or the browser reference/restore DTO.
            run = {k: row[k] for k in ("trace_id", "session_id", "user_id", "status",
                                      "workflow_version", "query", "skill_name")}
            run["metadata"] = metadata
            return {"run": run, "latest": latest, "source_version": version,
                    "presentations": selected}
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
            if target is None and type(row["query"]) is str:
                # The owned source query is part of source_version. Bind only
                # an explicit, single, unqualified target in this transaction;
                # never recover one from browser hints or other run history.
                request = analyze_target_request(row["query"])
                if request.explicit and not request.needs_clarification:
                    target = request.targets[0]
            by_id = {item["id"]: item for item in latest.values()}
            decoded, ordered, evidence, warnings = {}, [], [], []
            for selection in selections:
                oid = selection["observation_id"]
                if oid not in decoded:
                    checkpoint = by_id[oid]
                    if checkpoint["workflow_version"] != row["workflow_version"]:
                        raise ValueError("candidate workflow version mismatch")
                    candidates, result, eid = _candidates(checkpoint, trace_id, row=row, run_metadata=metadata)
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
