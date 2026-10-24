"""Immutable preparation for the complete Web domain; never executes science."""
from dataclasses import dataclass
import json

from src.agent.contracts import AgentContext
from src.agent.contracts.generation_request import preflight_generation_request
from src.agent.contracts.resolved_molecule import ResolvedScientificMolecule
from src.agent.contracts.web_requirements import WEB_PROFILE_REVISION, WEB_TOOLS, WebTaskRequirements
from src.agent.scientific_session.decision_bounds import context_value
from src.agent.scientific_session.decision_history import history_pairs
from src.agent.scientific_session.web_requirements import compile_web_requirements, required_web_tools
from .decision_request import DecisionAdmissionError, validate_request_envelope


@dataclass(frozen=True)
class PreparedWebDecision:
    _context_json: str
    request_kind: str
    allowed_tools: frozenset[str]
    required_tools: frozenset[str]
    requirements: WebTaskRequirements
    config_generation: str | None
    binding_profile: str = WEB_PROFILE_REVISION

    @property
    def context(self):
        raw = json.loads(self._context_json)
        if raw['resolved_molecule'] is not None:
            raw['resolved_molecule'] = ResolvedScientificMolecule(**raw['resolved_molecule'])
        return AgentContext(**raw)


def prepare_web_request(payload, *, session_id, trace_id, references=None,
                        config_generation=None, history=(), max_output_tokens=1500,
                        input_max_chars=24000, history_max_chars=2000):
    envelope = validate_request_envelope(payload, session_id=session_id, trace_id=trace_id,
                                        config_generation=config_generation)
    try:
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 8192:
            raise DecisionAdmissionError('invalid_decision_model_options')
        if any(type(value) is not int or value < 0 for value in (input_max_chars, history_max_chars)):
            raise DecisionAdmissionError('invalid_decision_model_options')
        selected = None
        query = envelope.query
        # The envelope's default is not caller authority. Preserve omitted/null
        # counts so canonical prose parsing can still determine the plan count.
        count_supplied = payload.get('mol_count') is not None
        requested_count = preflight_generation_request(query, envelope.mol_count,
            count_supplied=count_supplied, field='mol_count')
        from src.agent.utils.validators import InputValidator
        molecular_input = InputValidator().analyze_molecular_input(query)
        if molecular_input.blocks_execution:
            raise DecisionAdmissionError('invalid_molecular_input' if molecular_input.validation_available
                                         else 'molecular_validation_unavailable')
        if references is not None:
            try:
                selected = references.resolve(query, envelope.reference, envelope.selection,
                    session_id=session_id, enable_tools=envelope.enable_tools)
                query = references.continuation_query(query, selected, session_id=session_id)
            except ValueError:
                raise DecisionAdmissionError('scientific_reference_unavailable') from None
        elif envelope.reference is not None or envelope.selection is not None:
            raise DecisionAdmissionError('scientific_reference_unavailable')
        context = AgentContext(query, trace_id, user_id=session_id, session_id=session_id,
            temperature=envelope.temperature, mol_count=envelope.mol_count,
            memory=history_pairs(list(history)), resolved_molecule=selected,
            metadata={'capabilities': {'scientific_tools': envelope.enable_tools,
                                       'rag': envelope.enable_rag},
                      'rag_count': envelope.rag_count,
                      'decision_output_limit': max_output_tokens,
                      'decision_input_max_chars': input_max_chars,
                      'decision_history_max_chars': history_max_chars})
        if count_supplied:
            context.metadata['requested_count'] = requested_count
        allowed = WEB_TOOLS
        if not envelope.enable_tools:
            allowed &= {'rag_search'}
        if not envelope.enable_rag:
            allowed -= {'rag_search'}
        requirements = compile_web_requirements(context, allowed_tools=allowed)
        required = required_web_tools(requirements)
        # The model may select only actions compiled from this original request.
        return PreparedWebDecision(json.dumps(context_value(context), ensure_ascii=False,
            allow_nan=False), 'scientific' if required else 'chat', required, required,
            requirements, envelope.config_generation)
    except DecisionAdmissionError:
        raise
    except (ValueError, TypeError) as exc:
        # Forward only this closed admission reason, never exception text or
        # diagnostics from validators, generation preflight or dependencies.
        if type(exc) is ValueError and exc.args in (
                ('molecular_input_required',), ('target_clarification_required',)):
            raise DecisionAdmissionError(exc.args[0]) from None
        raise DecisionAdmissionError('request_clarification_required') from None
