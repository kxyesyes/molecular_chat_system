"""Optional live sidecar; storage/projection failure cannot change completion."""
import asyncio
import logging
import time

from src.agent.presentation.evidence_report import build_evidence_report

logger = logging.getLogger(__name__)


def _report_execution(execution):
    """Add only the server-owned workflow envelope needed by the projection."""
    if isinstance(execution.get("workflow_plan"), dict):
        return execution
    metadata = execution.get("metadata")
    requirements = metadata.get("task_requirements") if isinstance(metadata, dict) else None
    if not isinstance(requirements, dict) or requirements.get("profile") != "medchat-web-v1":
        return execution
    workflow_name = requirements.get("workflow_name")
    plan_metadata = requirements.get("metadata")
    if (workflow_name not in {"molecular_design", "target_driven_design", "hit_to_lead_optimization"}
            or not isinstance(plan_metadata, dict)):
        return execution
    return {**execution, "workflow_plan": {
        "workflow_name": workflow_name,
        "metadata": dict(plan_metadata),
    }}


async def prepare_report_event(store, execution, candidate_events, *, session_id):
    def prepare():
        report_execution = _report_execution(execution)
        references = [e["reference"] for e in candidate_events if "reference" in e]
        logger.info("Scientific report projection start trace_id=%s references=%s",
                    execution.get("trace_id"), len(references))
        if store is None or not references:
            logger.warning("Scientific report projection skipped trace_id=%s store=%s references=%s",
                           execution.get("trace_id"), store is not None, len(references))
            return None
        args = dict(session_id=session_id, references=references)
        report = None
        snapshot = None
        for attempt in range(3):
            snapshot = store.get_scientific_report_snapshot(execution.get("trace_id"), **args)
            report = build_evidence_report(snapshot, report_execution, candidate_events)
            if report is not None or attempt == 2:
                break
            # Checkpoints can commit just after the candidate projection is
            # emitted. Retry only an unavailable projection; a later source
            # mutation is still rejected by the existing version check below.
            time.sleep(0.05)
        if report is None:
            logger.warning("Scientific report projection unavailable trace_id=%s",
                           execution.get("trace_id"))
            return None
        current = store.get_scientific_report_snapshot(execution.get("trace_id"), **args)
        if (current is None or current["source_version"] != snapshot["source_version"]
                or current["presentations"] != snapshot["presentations"]):
            logger.warning("Scientific report projection changed before emit trace_id=%s",
                           execution.get("trace_id"))
            return None
        return report
    try:
        return await asyncio.to_thread(prepare)
    except Exception as exc:
        logger.warning("Scientific report projection failed trace_id=%s error_type=%s",
                       execution.get("trace_id"), type(exc).__name__)
        return None  # No raw diagnostics/credentials in frames or logs.
