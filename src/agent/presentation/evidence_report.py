"""Live static-workflow projection. Stored observations, not model prose, are authority.

No tool execution, result repair, ledger writes, or property recalculation here.
Temporary ToolResult/ledger instances below verify identities on detached copies.
"""
import json
from hashlib import sha256

from src.agent.contracts.scientific_references import reference_json
from src.agent.contracts.scientific_report import DESCRIPTORS, descriptor_values, validate_report
from src.agent.evidence.ledger import EvidenceLedger
from src.agent.orchestrators import WorkflowOrchestrator
from src.agent.persistence.scientific_references import _candidates
from src.agent.persistence.redaction import sanitize_sensitive_text
from src.agent.planning.bindings import BindingResolver
from src.agent.tooling.analysis_contract import _AnalysisRawOutput


def _check(condition):
    if not condition:
        raise ValueError("report source proof unavailable")


def _safe(text, limit=128):
    clean, changed = sanitize_sensitive_text(text, max_chars=limit)
    return clean if clean and not changed else None


def _accepted(cp, run, sequence, ledger):
    raw = json.loads(cp["output_json"])
    reference_json(raw)
    _check(cp["trace_id"] == run["trace_id"] and cp["workflow_version"] == run["workflow_version"])
    _check(cp["status"] in {"succeeded", "partial"} and cp["error_json"] is None
           and raw["success"] is True and raw["status"] == cp["status"] and raw.get("error") is None)
    expected = {**raw, "tool_name": cp["tool_name"]}
    matches = [o for o in sequence if o.get("quality", {}).get("step_id") == cp["step_id"]]
    _check(len(matches) == 1)
    observed = {k: v for k, v in matches[0].items() if k != "step_id"}
    _check(reference_json(observed) == reference_json(expected))
    _check(raw["quality"]["step_id"] == cp["step_id"])
    _check(raw["quality"].get("demo_mode", False) is False
           and raw["quality"].get("fallback_used", False) is False)
    result = WorkflowOrchestrator._result_from_checkpoint(cp["tool_name"], {"output": raw, "status": cp["status"]})
    p = result.provenance
    _check(p is not None and not p.demo_mode and not p.fallback_used
           and p.tool_name == cp["tool_name"] and p.tool_version == cp["tool_version"]
           and p.input_digest == cp["input_hash"])
    digest = EvidenceLedger.output_digest(raw["data"])
    _check(p.output_digest is None or p.output_digest == digest)
    verifier = EvidenceLedger(run["trace_id"])
    eid = verifier.register_tool_result(cp["step_id"], cp["input_hash"], result)
    _check(eid == raw["quality"]["evidence_id"])
    if ledger is not None:
        _check(type(ledger) is list)
        records = [e for e in ledger if e.get("evidence_id") == eid]
        _check(len(records) == 1 and reference_json(records[0]) == reference_json(verifier.get(eid)))
    if cp["tool_name"] == "llm_molecular_generator":
        _candidates(cp, run["trace_id"])
    elif cp["tool_name"] == "property_calculator":
        _AnalysisRawOutput.model_validate(raw, context={"tool_name": "property_calculator"})
    source = dict(observation_id=cp["id"], step_id=cp["step_id"], tool_name=cp["tool_name"],
        tool_version=cp["tool_version"], evidence_id=eid, status=cp["status"],
        input_digest=cp["input_hash"], data_digest=digest,
        observation_digest=sha256(reference_json(expected).encode("utf-8")).hexdigest(),
        output_digest_origin="recorded" if p.output_digest else "checkpoint_snapshot",
        model_name=_safe(p.model_name), model_version=_safe(p.model_version),
        method="rdkit_descriptors" if cp["tool_name"] == "property_calculator" else None,
        backend_version=None)
    _check(all(_safe(source[k]) == source[k] for k in
               ("observation_id", "step_id", "tool_name", "tool_version", "evidence_id")))
    return {"checkpoint": cp, "raw": raw, "source": source, "metadata": json.loads(cp["metadata_json"])}


def _property_rows(prop, generator, run):
    cp, raw = prop["checkpoint"], prop["raw"]
    key = generator["raw"]["quality"]["output_key"]
    _check(prop["metadata"].get("candidate_source") == key)
    data = generator["raw"]["data"]
    bound = BindingResolver().resolve(f"$.outputs.{key}", "smiles_text", {}, {key: data})
    _check(WorkflowOrchestrator._input_hash(bound) == cp["input_hash"])
    from rdkit import Chem
    candidates = {c["candidate_id"]: c for c in data["candidates"]}
    rows = {}
    for index, row in enumerate(raw["data"]):
        cid = row.get("candidate_id")
        _check(cid in candidates and cid not in rows)
        molecule = Chem.MolFromSmiles(row["smiles"])
        _check(molecule is not None and Chem.MolToSmiles(molecule, canonical=True) == candidates[cid]["canonical_smiles"])
        values = {k: row["properties"].get(k) for k in DESCRIPTORS}
        descriptor_values(values)
        rows[cid] = (index, row, values)
    alignment = raw["quality"].get("candidate_alignment")
    _check(type(alignment) is dict and set(alignment) == {
        "source_count", "aligned_count", "missing_candidate_ids", "discarded_count"})
    _check(type(alignment["source_count"]) is int and alignment["source_count"] == len(candidates)
           and type(alignment["aligned_count"]) is int and alignment["aligned_count"] == len(rows)
           and alignment["missing_candidate_ids"] == [c for c in candidates if c not in rows]
           and type(alignment["discarded_count"]) is int and alignment["discarded_count"] >= 0)
    _check(raw["status"] == "partial" or (not alignment["missing_candidate_ids"] and alignment["discarded_count"] == 0))
    return rows


def _empty_ranking(reason="missing_source"):
    return dict(state="not_provided", reason_code=reason, source_observation_id=None,
        generator_observation_id=None, requested_top_n=None, ranked_candidate_count=None,
        top_candidates=[], unrankable_candidates=[])


def _ranking(accepted, outputs, generators, run, displayed):
    ranks = [s for s in accepted.values() if s["source"]["tool_name"] == "candidate_ranker"]
    if not ranks:
        return _empty_ranking()
    if len(ranks) != 1:
        return _empty_ranking("ambiguous_source")
    rank = ranks[0]
    try:
        meta = rank["metadata"]
        _check(meta.get("workflow_output_keys") == ["molecules", "properties", "admet", "activity"]
               and meta.get("workflow_optional_output_keys") == ["admet", "activity"]
               and meta.get("workflow_metadata_keys") == ["docking_top_n"])
        generators = [g for g in generators if g["raw"]["quality"].get("output_key") == "molecules"]
        _check(len(generators) == 1)
        generator = generators[0]
        # Refuse ranking unless the required properties are independently aligned too.
        props = [s for s in accepted.values() if s["raw"]["quality"].get("output_key") == "properties"]
        _check(len(props) == 1 and props[0]["source"]["tool_name"] == "property_calculator")
        _property_rows(props[0], generator, run)
        request = {"query": run["query"], "metadata": {"docking_top_n": meta["docking_top_n"]}}
        bound = BindingResolver().resolve("$.workflow", "identity", request, outputs,
            workflow_output_keys=meta["workflow_output_keys"],
            workflow_optional_output_keys=meta["workflow_optional_output_keys"],
            workflow_metadata_keys=meta["workflow_metadata_keys"])
        _check(WorkflowOrchestrator._input_hash(bound) == rank["checkpoint"]["input_hash"])
        raw = rank["raw"]["data"]
        _check(rank["raw"]["quality"].get("output_contract") == "CandidateRanking@1")
        ranked = raw["ranked_candidates"]
        _check(type(raw["requested_top_n"]) is int and raw["requested_top_n"] == meta["docking_top_n"]
               and type(raw["ranked_candidate_count"]) is int and raw["ranked_candidate_count"] == len(ranked)
               and raw["top_candidates"] == ranked[:raw["requested_top_n"]])
        canonical = {c["candidate_id"]: c["canonical_smiles"] for c in generator["raw"]["data"]["candidates"]}
        seen = set()
        for row in ranked + raw["unrankable_candidates"]:
            _check(row["candidate_id"] not in seen and canonical.get(row["candidate_id"]) == row["canonical_smiles"])
            seen.add(row["candidate_id"])
        _check(seen == set(canonical))
        oid = generator["checkpoint"]["id"]
        # Never filter/re-sort Top-N into a different positive result.
        _check(all((oid, r["candidate_id"]) in displayed for r in raw["top_candidates"] + raw["unrankable_candidates"]))
        top = [{k: r[k] for k in ("candidate_id", "canonical_smiles", "score", "missing_evidence", "ranking_evidence")}
               for r in raw["top_candidates"][:32]]
        return dict(state="partial" if rank["raw"]["status"] == "partial" or any(r["missing_evidence"] for r in top) else "available",
            reason_code="partial_source" if rank["raw"]["status"] == "partial" or any(r["missing_evidence"] for r in top) else "none",
            source_observation_id=rank["checkpoint"]["id"], generator_observation_id=oid,
            requested_top_n=raw["requested_top_n"], ranked_candidate_count=len(ranked),
            top_candidates=top, unrankable_candidates=raw["unrankable_candidates"][:32])
    except (ValueError, TypeError, KeyError, AttributeError):
        return _empty_ranking("input_unverifiable")


def build_evidence_report(snapshot, execution, candidate_events):
    """Fail closed; callers may keep all legacy frames when this returns None."""
    try:
        if snapshot is None:
            return None
        run = snapshot["run"]
        _check(execution.get("trace_id") == run["trace_id"] and run["skill_name"] == "target_driven_design")
        status = "partial" if run["status"] == "partial" else "completed"
        _check(execution.get("status") == status)
        plan = execution.get("workflow_plan", {})
        _check(plan.get("workflow_name") == "target_driven_design")
        sequence = execution.get("tool_result_sequence")
        reference_json(sequence)
        _check(type(sequence) is list and len(sequence) <= 256)
        ledger = execution.get("metadata", {}).get("evidence_ledger")
        if ledger is None and execution.get("agent_result") is not None:
            ledger = execution["agent_result"].metadata.get("evidence_ledger")
        accepted, failures = {}, {}
        for cp in snapshot["latest"].values():
            try:
                accepted[cp["id"]] = _accepted(cp, run, sequence, ledger)
            except (ValueError, TypeError, KeyError, AttributeError):
                failures[cp["id"]] = "source_failed" if cp["status"] not in {"succeeded", "partial"} else "source_mismatch"
        _check(len(accepted) <= 24)
        # No output-key collapse: duplicate providers invalidate that binding.
        keys = {}
        for s in accepted.values():
            key = s["raw"]["quality"].get("output_key")
            if type(key) is str:
                keys.setdefault(key, []).append(s)
        outputs = {k: items[0]["raw"]["data"] for k, items in keys.items() if len(items) == 1}
        collections, generations, rows, generators = [], [], [], []
        displayed = set()
        for view in snapshot["presentations"]:
            ref = {"trace_id": run["trace_id"], "presentation_id": view["presentation_id"],
                "revision": view["revision"], "ordered_keys": [[r["observation_id"], r["candidate"]["candidate_id"]]
                                                         for r in view["ordered_candidates"]]}
            events = [e for e in candidate_events if e.get("reference") == ref]
            _check(len(events) == 1 and events[0]["trace_id"] == run["trace_id"])
            oids = {r["observation_id"] for r in view["ordered_candidates"]}
            _check(len(oids) == 1)
            oid = next(iter(oids))
            generator = accepted[oid]
            _check(generator["source"]["tool_name"] == "llm_molecular_generator")
            data = generator["raw"]["data"]
            _check(reference_json(events[0]["candidate_set"]) == reference_json(data))
            _check(all(r["candidate"] in data["candidates"] for r in view["ordered_candidates"]))
            generators.append(generator)
            displayed.update(map(tuple, ref["ordered_keys"]))
            collections.append({"reference": ref, "generator_observation_id": oid})
            generations.append(dict(source_observation_id=oid, status=generator["raw"]["status"],
                **{k: data[k] for k in ("requested_count", "valid_count", "unique_count", "invalid_count", "duplicate_count")},
                displayed_count=len(ref["ordered_keys"])))
            key = generator["raw"]["quality"].get("output_key")
            dynamic = any(s["raw"]["quality"].get("request_input_digest") is not None for s in accepted.values())
            props = [s for s in accepted.values() if s["source"]["tool_name"] == "property_calculator"
                     and s["metadata"].get("candidate_source") == key]
            reason, mapped, prop = "missing_source", {}, None
            if dynamic:
                reason = "unsupported_binding"
            elif len(props) > 1 or len(keys.get(key, [])) != 1:
                reason = "ambiguous_source"
            elif props:
                try:
                    mapped = _property_rows(props[0], generator, run)
                    prop = props[0]
                except (ValueError, TypeError, KeyError, AttributeError):
                    reason = "input_unverifiable"
            elif any(cp["tool_name"] == "property_calculator" for cp in snapshot["latest"].values()):
                reason = "source_unavailable"
            for item in view["ordered_candidates"]:
                candidate = item["candidate"]
                row = dict(generator_observation_id=oid, candidate_id=candidate["candidate_id"],
                    canonical_smiles=candidate["canonical_smiles"], source_observation_id=None,
                    source_row_index=None, row_digest=None, state="not_provided", reason_code=reason,
                    values=dict.fromkeys(DESCRIPTORS))
                if prop is not None and candidate["candidate_id"] in mapped:
                    index, raw, values = mapped[candidate["candidate_id"]]
                    partial = prop["raw"]["status"] == "partial" or any(v is None for v in values.values())
                    row.update(source_observation_id=prop["checkpoint"]["id"], source_row_index=index,
                        row_digest=EvidenceLedger.output_digest(raw), values=values,
                        state="partial" if partial else "available", reason_code="partial_source" if partial else "none")
                elif prop is not None:
                    row["reason_code"] = "row_missing"
                rows.append(row)
        _check(collections and len(displayed) <= 32)
        steps = []
        for cp in snapshot["latest"].values():
            source = accepted.get(cp["id"])
            steps.append(dict(step_id=cp["step_id"], tool_name=cp["tool_name"],
                status=cp["status"] if source or cp["status"] in {"failed", "rejected", "cancelled"} else "unknown",
                source_observation_id=cp["id"] if source else None,
                reason_code="none" if source else failures.get(cp["id"], "source_unavailable"),
                message="工具执行记录；不等同于实验验证" if source else "未取得可验证的成功工具证据"))
        # Retain skipped tool outcomes without inventing successful observations.
        for skipped in execution.get("metadata", {}).get("skipped_steps", []):
            steps.append(dict(step_id=skipped["step_id"], tool_name=skipped.get("tool_name") or "未提供", status="skipped",
                source_observation_id=None, reason_code="source_unavailable", message="步骤已跳过"))
        target = _safe(plan.get("metadata", {}).get("target_hint"))
        warnings = ["计算描述符及候选优先级不代表实验验证、活性、疗效或对接结合能。",
                    "性质数字仅对本次实时证据展示有效；刷新后不恢复数字。"]
        for warning in execution.get("warnings", []):
            text = _safe(warning, 256)
            if text:
                warnings.append(text)
        ranking = _empty_ranking("unsupported_binding") if dynamic else _ranking(accepted, outputs, generators, run, displayed)
        report = dict(type="scientific_report", schema_version="1", trace_id=run["trace_id"],
            source_version=snapshot["source_version"], run_status=status,
            target={"label": target, "origin": "workflow_plan" if target else "not_provided"},
            sources=[s["source"] for s in accepted.values()], generations=generations,
            collections=collections, property_rows=rows, steps=steps[:32], ranking=ranking, warnings=warnings[:32],
            omitted=dict(generations=0, steps=max(0, len(steps)-32), property_rows=0,
                         ranking_rows=0, warnings=max(0, len(warnings)-32)))
        report["projection_id"] = sha256(reference_json(report).encode("utf-8")).hexdigest()
        return validate_report(report)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return None
