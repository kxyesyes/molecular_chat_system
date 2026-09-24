"""Optional live sidecar; storage/projection failure cannot change completion."""
import asyncio

from src.agent.presentation.evidence_report import build_evidence_report


async def prepare_report_event(store, execution, candidate_events, *, session_id):
    def prepare():
        references = [e["reference"] for e in candidate_events if "reference" in e]
        if store is None or not references:
            return None
        args = dict(session_id=session_id, references=references)
        snapshot = store.get_scientific_report_snapshot(execution.get("trace_id"), **args)
        report = build_evidence_report(snapshot, execution, candidate_events)
        if report is None:
            return None
        current = store.get_scientific_report_snapshot(execution.get("trace_id"), **args)
        if (current is None or current["source_version"] != snapshot["source_version"]
                or current["presentations"] != snapshot["presentations"]):
            return None
        return report
    try:
        return await asyncio.to_thread(prepare)
    except Exception:
        return None  # No raw diagnostics/credentials in frames or logs.
