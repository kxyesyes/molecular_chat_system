import pytest

from src.web.web_business import WebTurnProjection
from src.web.web_request import prepare_web_request
from tests.agent.test_pi_web_activity_context import activity_evidence
from tests.agent.test_pi_web_candidate_context import candidate_evidence


def descriptors(owner):
    return dict(session_id=owner, trace_id="trace", evidence_id="value", rows=[
        dict(smiles="CCN", logp=-0.035, molecular_weight=45.08),
        dict(smiles="CCO", logp=-0.001, molecular_weight=46.07)])


@pytest.mark.parametrize("field,record,context_key", [
    ("activity_evidence", activity_evidence, "prior_activity_context"),
    ("candidate_evidence", candidate_evidence, "prior_candidate_context"),
    ("batch_property_evidence", descriptors, "prior_candidate_context"),
])
def test_freeform_discussion_sees_only_owned_facts_without_new_tool_permission(field, record, context_key):
    question = "写成一句可以在组会上汇报的结论。"
    for record_owner in ("owner", "other"):
        projection = WebTurnProjection(question, "owner", [{"user": "analysis", field: record(record_owner)}])
        def prepare(query, budget=24000):
            return prepare_web_request({"message": query}, session_id="owner", trace_id="next",
                implicit_records=projection.implicit_records, history_max_chars=budget)
        discussion = prepare(question)
        assert bool(discussion.context.metadata.get(context_key)) == (record_owner == "owner")
        assert discussion.request_kind == "chat" and not discussion.required_tools
        if field == "batch_property_evidence" and record_owner == "owner":
            rows = discussion.context.metadata[context_key]["candidates"]
            assert [(r["canonical_smiles"], r["properties"]["molecular_weight"]) for r in rows] == [
                ("CCN", 45.08), ("CCO", 46.07)]
        assert context_key not in prepare(question, 1).context.metadata
        computation = prepare("请计算 SMILES CCO 的分子量")
        assert computation.required_tools and context_key not in computation.context.metadata
