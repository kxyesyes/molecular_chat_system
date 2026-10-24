"""Socket-local scientific projections; no model or workflow execution.

The runtime creates one object per accepted turn. Its detached entry is published
only after terminal delivery, separately from ordinary model conversation history.
"""
from copy import deepcopy
import json

from src.agent import evidence_followup
from src.agent.contracts import RunOutcome
from . import property_comparison
from .property_constraints import generation_constraint_report
from .ranked_target_structures import rank_followup_kind, ranked_structure_followup


class WebTurnProjection:
    def __init__(self, query, session_id, history, *, reverse_boundary=None):
        self.query = query
        self.session_id = session_id
        self.history = deepcopy(history[-1:])
        self.reverse_boundary = reverse_boundary
        self.entry = {'user': query, 'agent_used': False}

    def execution_query(self):
        return property_comparison.corrected_property_query(self.query, self.history)

    def followup(self, *, enable_tools):
        if rank_followup_kind(self.query) is not None:
            view = ranked_structure_followup(self.query, self.history,
                session_id=self.session_id, enable_tools=enable_tools, boundary=self.reverse_boundary)
            if view.get('context') is not None:
                self.entry['reverse_target_evidence'] = view['context']
            return {'content': view['content'],
                    'status': 'completed' if view['status'] == 'succeeded' else view['status']}
        view = evidence_followup.followup_answer(self.query, self.history, self.session_id)
        if view is not None:
            return {'content': view['final_answer'], 'status': view['status']}
        return None

    def capture(self, result, *, context, reverse_count=10):
        """Read only accepted Pi observations; never turn model prose into state."""
        if (result.metadata.get('binding_profile') != 'medchat-web-v1'
                or result.metadata.get('waiting_for_input')
                or result.outcome not in {RunOutcome.COMPLETED, RunOutcome.PARTIAL}):
            return None
        raw = {**result.to_legacy_dict(), 'trace_id': result.trace_id}
        if not raw['tool_result_sequence']:
            return None
        try:
            audit = json.loads(result.final_answer)
            cited = {block['evidence_id'] for block in audit['observations'] if 'data' in block}
        except (ValueError, TypeError, KeyError):
            return None
        # Uncited/failed results remain in the immutable audit and partial
        # diagnostics, but cannot authorize a later scientific follow-up.
        eligible = {**raw, 'tool_result_sequence': [item for item in raw['tool_result_sequence']
            if item.get('quality', {}).get('evidence_id') in cited]}
        query = context.query
        if context.resolved_molecule is not None:
            query = context.resolved_molecule.routing_query(query)
        self.entry['agent_used'] = True
        properties = property_comparison.accepted_property_evidence(eligible, query, self.session_id)
        if properties is not None:
            self.entry['property_evidence'] = properties
        activity = evidence_followup.accepted_activity_evidence(eligible, query, self.session_id)
        if activity is not None:
            self.entry['activity_evidence'] = activity
        if self.reverse_boundary is not None:
            pointer = self.reverse_boundary.capture(eligible, session_id=self.session_id)
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
            else:
                view = {'final_answer': content, 'status': raw['status'], 'warnings': []}
        constraints = generation_constraint_report(eligible, context.query)
        if constraints is not None:
            view = {**(view or {'status': raw['status'], 'warnings': []}), 'prefix': constraints}
        return view
