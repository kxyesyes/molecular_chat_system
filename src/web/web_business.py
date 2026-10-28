"""Socket-local scientific projections; no model or workflow execution.

The runtime creates one object per accepted turn. Its detached entry is published
only after terminal delivery, separately from ordinary model conversation history.
"""
from copy import deepcopy
import json
import logging
import re

from src.agent import evidence_followup
from src.agent.contracts import RunOutcome
from . import property_comparison
from .property_constraints import generation_constraint_report
from .ranked_target_structures import rank_followup_kind, ranked_structure_followup


logger = logging.getLogger(__name__)


_MODEL_HISTORY_CLAIM = re.compile(
    r'\b(?:previous|prior|earlier)\b|\blast\s+(?:run|result|prediction|value|measurement|experiment|calculation|time)\b|上次|上一轮|之前|刚才|'
    r'\b(?:we\s+(?:obtained|measured|calculated|predicted)|you\s+(?:asked|measured|obtained))\b.{0,100}\b(?:was|is|were)\s+[+-]?\d|'
    r'(?:\byour\b|你的|您的).{0,100}(?:pIC50|IC50|活性|数值|预测).{0,100}'
    r'(?:\b(?:was|is|were)\b|是|为|=|[:：])\s*(?:(?:approximately|about|around|roughly|近似|约)\s*)?[+-]?\d', re.I | re.S)


class WebTurnProjection:
    def __init__(self, query, session_id, history, *, reverse_boundary=None):
        from src.agent.activity_evidence_inspection import split_concept_prior_value
        self.query = query
        self.session_id = session_id
        # Candidate evidence may be distributed across the design turn and a
        # later single-candidate ADMET turn; retain a small bounded window for
        # read-only reconciliation while keeping ordinary history projection
        # compact.
        self.history = deepcopy(history[-8:])
        self.reverse_boundary = reverse_boundary
        self.concept_history = split_concept_prior_value(query)
        self.safe_concept_history = None
        self.entry = {'user': query, 'agent_used': False}

    def execution_query(self):
        if self.concept_history is not None:
            return self.concept_history[0]
        return property_comparison.corrected_property_query(self.query, self.history)

    def followup(self, *, enable_tools, enable_rag=False):
        if self.concept_history is not None:
            return None  # The concept still needs an ordinary model response.
        from src.agent.candidate_evidence_followup import followup_answer as candidate_followup
        candidate_view = candidate_followup(self.query, self.history, self.session_id)
        logger.info("candidate evidence follow-up history=%d candidate_entries=%d result=%s",
                    len(self.history), sum(isinstance(item, dict) and
                    isinstance(item.get('candidate_evidence'), dict) for item in self.history),
                    candidate_view is not None)
        if candidate_view is not None:
            return candidate_view
        if rank_followup_kind(self.query) is not None:
            view = ranked_structure_followup(self.query, self.history,
                session_id=self.session_id, enable_tools=enable_tools, boundary=self.reverse_boundary)
            if view.get('context') is not None:
                self.entry['reverse_target_evidence'] = view['context']
            return {'content': view['content'],
                    'status': 'completed' if view['status'] == 'succeeded' else view['status']}
        view = evidence_followup.followup_answer(self.query, self.history, self.session_id)
        if view is not None:
            if view.get('activity_evidence') is not None:
                self.entry['activity_evidence'] = deepcopy(view['activity_evidence'])
            return {'content': view['final_answer'],
                    'summary_content': view.get('summary_answer', view['final_answer']),
                    'status': view['status']}
        return None

    def capture(self, result, *, context, reverse_count=10, reverse_restricted=False):
        """Read only accepted Pi observations; never turn model prose into state."""
        if (result.metadata.get('binding_profile') != 'medchat-web-v1'
                or result.metadata.get('waiting_for_input')
                or not isinstance(result.outcome, RunOutcome)
                or result.outcome == RunOutcome.CANCELLED
                or (self.concept_history is None
                    and result.outcome not in {RunOutcome.COMPLETED, RunOutcome.PARTIAL})):
            return None
        if self.concept_history is not None:
            if context.query != self.concept_history[0]:
                return None
            view = evidence_followup.followup_answer(
                self.concept_history[1], self.history, self.session_id)
            if view is None:
                return None
            if view.get('activity_evidence') is not None:
                self.entry['activity_evidence'] = deepcopy(view['activity_evidence'])
            # Even with no historical question in the model input, never show
            # a model-authored claim about a user's earlier values as evidence.
            if result.tool_results or result.outcome != RunOutcome.COMPLETED or not result.success:
                # A zero-step concept plan must not normally produce an
                # observation. Retain history rather than silently dropping it.
                concept = '概念解释本轮未能安全完成，请分开请求后重试。'
            else:
                concept = result.final_answer
            if _MODEL_HISTORY_CLAIM.search(concept):
                concept = '概念解释暂不可用；模型回答包含未经验证的历史结果表述。'
            self.safe_concept_history = concept + '\n历史数值仅保留于科学记录。'
            answer = view['final_answer'] + '\n\n概念解释\n\n' + concept
            summary = view.get('summary_answer', view['final_answer']) + '\n\n概念解释\n\n' + concept
            return {'final_answer': answer, 'summary_answer': summary,
                    'suppress_raw_answer': True,
                    'mixed_history_failure': result.outcome not in {RunOutcome.COMPLETED, RunOutcome.PARTIAL},
                    'status': (result.outcome.value if result.outcome not in {RunOutcome.COMPLETED, RunOutcome.PARTIAL}
                               else 'completed' if (view['status'] == 'completed'
                                                     and concept == result.final_answer) else 'partial'),
                    'warnings': []}
        raw = {**result.to_legacy_dict(), 'trace_id': result.trace_id}
        if not raw['tool_result_sequence']:
            return None
        query = context.query
        if context.resolved_molecule is not None:
            query = context.resolved_molecule.routing_query(query)
        # Candidate/property evidence is independently projected from accepted
        # structured observations. Target-design answers are often rendered as
        # Markdown/scientific reports rather than the legacy JSON audit text;
        # requiring that text to be JSON would silently lose reusable evidence.
        from src.agent.candidate_evidence_followup import accepted_candidate_evidence
        candidate_sequence = [item for item in raw['tool_result_sequence']
            if isinstance(item, dict) and item.get('status') == 'succeeded'
            and item.get('success') is True and item.get('error') is None
            and isinstance(item.get('quality'), dict)
            and item['quality'].get('validated') is True
            and item['quality'].get('scientific_usable') is not False]
        # Ranking reuse is authorized by the server-owned checkpoint and the
        # current reverse-source receipt, not by model prose. Keep this
        # separately from the cited closure used by generic follow-ups: a
        # model may omit an evidence_id even though the reverse observation
        # itself is complete and independently revalidated by the boundary.
        reverse_sequence = [item for item in candidate_sequence
            if item.get('tool_name') == 'reverse_target_predictor']
        candidate_snapshot = {**raw, 'tool_result_sequence': candidate_sequence}
        candidate_evidence = accepted_candidate_evidence(candidate_snapshot, query, self.session_id)
        logger.info("candidate evidence capture observations=%d accepted=%d candidates=%d",
                    len(raw['tool_result_sequence']), len(candidate_sequence),
                    len(candidate_evidence.get('candidates', [])) if candidate_evidence else 0)
        if candidate_evidence is not None:
            self.entry['candidate_evidence'] = candidate_evidence
        try:
            audit = json.loads(result.final_answer)
            cited = {block['evidence_id'] for block in audit['observations'] if 'data' in block}
        except (ValueError, TypeError, KeyError):
            return None
        # Uncited/failed results remain in the immutable audit and partial
        # diagnostics, but cannot authorize a later scientific follow-up.
        eligible = {**raw, 'tool_result_sequence': [item for item in raw['tool_result_sequence']
            if item.get('quality', {}).get('evidence_id') in cited]}
        self.entry['agent_used'] = True
        properties = property_comparison.accepted_property_evidence(eligible, query, self.session_id)
        if properties is not None:
            self.entry['property_evidence'] = properties
        activity = evidence_followup.accepted_activity_evidence(eligible, query, self.session_id)
        if activity is not None:
            self.entry['activity_evidence'] = activity
        from src.agent.candidate_evidence_followup import accepted_candidate_evidence
        candidate_evidence = accepted_candidate_evidence(eligible, query, self.session_id)
        if candidate_evidence is not None:
            self.entry['candidate_evidence'] = candidate_evidence
        if self.reverse_boundary is not None:
            reverse_snapshot = {**raw, 'tool_result_sequence': reverse_sequence}
            pointer = self.reverse_boundary.capture(reverse_snapshot, session_id=self.session_id)
            if pointer is not None:
                self.entry['reverse_target_evidence'] = pointer
        view = evidence_followup.comparison_answer(self.query, eligible, self.session_id)
        tools = {item['tool_name'] for item in raw['tool_result_sequence']}
        if tools == {'property_calculator'}:
            view = property_comparison.descriptor_result(
                self.query, properties, self.history, self.session_id) or view
        if tools == {'reverse_target_predictor'}:
            pointer = self.entry.get('reverse_target_evidence')
            content = (self.reverse_boundary.render_prefix(pointer, session_id=self.session_id,
                       count=reverse_count) if pointer is not None else None)
            if content is None:
                self.entry.pop('reverse_target_evidence', None)
                view = {'final_answer': '排名证据不可用：无法确认同会话、已接受且仍有效的来源；请重新运行反向寻靶。',
                        'status': 'partial', 'warnings': ['rank_source_unavailable']}
            elif reverse_restricted:
                notice = '当前不展示排名，请明确希望查看的排名范围。'
                view = {'final_answer': notice, 'summary_answer': notice + '完整排名包含在明细中。',
                        'summary_answer_without_details': notice,
                        'status': raw['status'], 'warnings': []}
            else:
                summary = self.reverse_boundary.render_prefix(pointer, session_id=self.session_id,
                    count=reverse_count, concise=True)
                if summary is None:
                    self.entry.pop('reverse_target_evidence', None)
                    view = {'final_answer': '排名证据不可用：无法确认同会话、已接受且仍有效的来源；请重新运行反向寻靶。',
                            'status': 'partial', 'warnings': ['rank_source_unavailable']}
                else:
                    view = {'final_answer': content, 'summary_answer': summary,
                            'summary_kind': 'reverse_target_rank_v1',
                            'status': raw['status'], 'warnings': []}
        constraints = generation_constraint_report(eligible, context.query)
        if constraints is not None:
            view = {**(view or {'status': raw['status'], 'warnings': []}), 'prefix': constraints}
        return view
