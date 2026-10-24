"""Bounded explicit server inputs, NOT authentication or scientific evidence.

Only the owner-validated caller may admit replies or restore a saved journal.
Task5 must authenticate store/owner/configuration/nonce before installing its
candidate. A checksum (even a consistent edited one) never proves provenance.
The eventual revision8 snapshot, including this journal, still has ONE 512KiB
ceiling; this helper does not grant another budget to records/messages/results.
"""
import json

from src.agent.contracts import AgentContext
from src.agent.contracts.resolved_molecule import ResolvedScientificMolecule
from src.agent.evidence import EvidenceLedger
from .decision_bounds import context_value, validate_json
from .decision_policy import DecisionBoundaryError


_ERROR = 'invalid_binding_inputs'
_LIMIT = 512 * 1024
_VARIABLE = frozenset({'query', 'resolved_molecule'})


def _wire(value):
    validate_json(value, max_bytes=_LIMIT, reason=_ERROR)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _context(value):
    selected = value['resolved_molecule']
    context = AgentContext(**{**value, 'resolved_molecule': (
        ResolvedScientificMolecule(**selected) if selected is not None else None)})
    context_value(context, query_content_bytes=True)
    return context


class BindingInputJournal:
    """Freeze every original field; only explicitly admitted query/selection vary.

    Entries and full contexts are detached native projections. Indexes are
    zero-based; the future loop maps proposal.input_turn - 1 to this boundary.
    None here means raw selection omission, not effective reference clearing;
    sequential scientific reduction belongs to the owned resolver.
    """

    def __init__(self, original_context):
        original = context_value(original_context, query_content_bytes=True)
        self._json = _wire(dict(version='1', original=original, entries=[dict(
            input_turn=0, query=original['query'], resolved_molecule=original['resolved_molecule'])]))

    @property
    def head_turn(self):
        return len(json.loads(self._json)['entries']) - 1

    def _turn(self, input_turn):
        if type(input_turn) is not int or not 0 <= input_turn <= self.head_turn:
            raise DecisionBoundaryError(_ERROR)
        return input_turn

    def export(self):
        return json.loads(self._json)

    def prefix(self, input_turn):
        turn = self._turn(input_turn)
        value = self.export()
        value['entries'] = value['entries'][:turn + 1]
        return value

    def prefix_digest(self, input_turn):
        return EvidenceLedger.output_digest(self.prefix(input_turn))

    def context(self, input_turn=None):
        turn = self.head_turn if input_turn is None else self._turn(input_turn)
        value = self.export()
        entry = value['entries'][turn]
        return _context({**value['original'], **{k: entry[k] for k in _VARIABLE}})

    def matches_context(self, context, *, input_turn=None):
        # Wire equality distinguishes native 1/1.0/True, including nested fields.
        return _wire(context_value(context, query_content_bytes=True)) == _wire(
            context_value(self.context(input_turn), query_content_bytes=True))

    def admit_context(self, context, *, input_turn):
        value = context_value(context, query_content_bytes=True)
        current = self.export()
        if (type(input_turn) is not int or input_turn not in (self.head_turn, self.head_turn + 1)
                or _wire({k: v for k, v in value.items() if k not in _VARIABLE}) !=
                _wire({k: v for k, v in current['original'].items() if k not in _VARIABLE})):
            raise DecisionBoundaryError(_ERROR)
        if input_turn == self.head_turn:
            if not self.matches_context(context):
                raise DecisionBoundaryError(_ERROR)
            return
        if input_turn >= 16:
            raise DecisionBoundaryError(_ERROR)
        current['entries'].append(dict(input_turn=input_turn,
            query=value['query'], resolved_molecule=value['resolved_molecule']))
        self._json = _wire(current)  # validate atomically before installation

    @classmethod
    def restore_trusted(cls, value, *, original_context):
        """Explicit trusted-caller reconstruction, never a browser deserializer.

        Validates structure/order/bounds and exact original context, not storage
        authenticity. The caller must validate every action prefix and proposal
        against sealed observations before granting any execution authority.
        """
        raw = _wire(value)
        candidate = cls(original_context)
        try:
            if (type(value) is not dict or set(value) != {'version', 'original', 'entries'}
                    or value['version'] != '1' or type(value['entries']) is not list
                    or not 1 <= len(value['entries']) <= 16
                    or _wire(value['original']) != _wire(candidate.export()['original'])):
                raise DecisionBoundaryError(_ERROR)
            for turn, entry in enumerate(value['entries']):
                if (type(entry) is not dict or set(entry) != {'input_turn', 'query', 'resolved_molecule'}
                        or type(entry['input_turn']) is not int or entry['input_turn'] != turn):
                    raise DecisionBoundaryError(_ERROR)
                candidate.admit_context(_context({**value['original'],
                    **{k: entry[k] for k in _VARIABLE}}), input_turn=turn)
            if candidate._json != raw:
                raise DecisionBoundaryError(_ERROR)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise DecisionBoundaryError(_ERROR) from None
        return candidate
