"""B1 immutable obligation preparation; no resolver or execution integration yet.

The caller must supply the ORIGINAL admitted context, including on continuation.
Clarification/resolution must not replace this snapshot or weaken its obligations.
No model/tool execution, trace creation, source authentication or store access.
"""
from types import SimpleNamespace

from src.agent.contracts.binding_requirements import (
    parse_binding_requirements, required_binding_tools,
)
from src.agent.contracts.decision_bindings import B1_TOOLS


_ERROR = 'invalid_binding_requirements'


def _tool_names(values):
    if type(values) not in (tuple, list, set, frozenset) or len(values) > 32:
        raise ValueError(_ERROR)
    if any(type(name) is not str or not name or len(name) > 64 for name in values):
        raise ValueError(_ERROR)
    return frozenset(values)


def _canonical_subjects(values, parser):
    from src.agent.tools.molecular_input import parse_molecular_smiles
    from .decision_requirements import _canonical

    subjects = []
    for value in values:
        # A requirement denotes exactly this complete structure, not a prompt.
        if (type(value) is not str or not value.isascii()
                or parse_molecular_smiles('SMILES: ' + value, parser) != [value]):
            raise ValueError(_ERROR)
        canonical = _canonical(value)
        if canonical is None or canonical in subjects:
            raise ValueError(_ERROR)
        subjects.append(canonical)
    return frozenset(subjects)


def prepare_binding_requirements(value, *, context, request_kind, allowed_tools, required_tools):
    """Validate original obligations and effective permissions before any actions.

    Missing molecules may be clarified later. Supplied invalid/duplicate/conflicting
    molecules cannot be replaced by fragments. The returned value retains original
    spelling, order, counts and targets; preparation never rewrites user intent.
    """
    try:
        requirements = parse_binding_requirements(value)
        from src.agent.contracts import AgentContext
        from .decision_bounds import validate_json
        if (type(context) is not AgentContext or type(context.query) is not str
                or type(context.metadata) is not dict):
            raise ValueError(_ERROR)
        # Bound only preparation's native input view. Lifecycle admission owns
        # the rest of AgentContext. Its legacy 16KiB JSON-string limit is not
        # the v2 retrieval query's 16KiB UTF-8 CONTENT limit (quotes/escapes differ).
        validate_json({'query': context.query, 'metadata': context.metadata},
                      max_bytes=65536, reason=_ERROR)
        if type(request_kind) is not str or request_kind not in ('chat', 'scientific'):
            raise ValueError(_ERROR)
        allowed = _tool_names(allowed_tools) & B1_TOOLS
        required = required_binding_tools(requirements, _tool_names(required_tools))
        capabilities = context.metadata.get('capabilities', {})
        if type(capabilities) is not dict:
            raise ValueError(_ERROR)
        scientific = capabilities.get('scientific_tools', True)
        rag = capabilities.get('rag', True)
        if type(scientific) is not bool or type(rag) is not bool:
            raise ValueError(_ERROR)
        effective = allowed - set(requirements.forbidden_tools)
        if not scientific:
            effective &= {'rag_search'}
        if not rag:
            effective -= {'rag_search'}
        if request_kind == 'chat':
            effective = frozenset()
        if not required <= effective:
            raise ValueError(_ERROR)

        retrieval = requirements.retrieval_result
        if retrieval is not None and retrieval.query != context.query:
            raise ValueError(_ERROR)
        target = requirements.target_result
        if target is not None and target.input == 'user' and target.query != context.query:
            from src.agent.contracts.target_request import analyze_target_request
            original_target = analyze_target_request(context.query)
            if original_target.needs_clarification or target.query not in original_target.targets:
                raise ValueError(_ERROR)

        groups = (*requirements.molecular_results, *requirements.analysis_results)
        molecular_tools = B1_TOOLS - {'rag_search', 'target_database_search'}
        if not required & molecular_tools:
            return requirements

        # Existing parsers/RDKit are loaded only when molecular obligations need
        # checking. They perform no scientific prediction or asset discovery.
        from src.agent.tools.base_tool import BaseMolecularTool
        from src.agent.tools.molecular_input import parse_molecular_smiles
        from .decision_inputs import has_explicit_molecule, effective_molecule, activity_input_target
        parser = BaseMolecularTool('binding_requirements', '')

        if 'activity_predictor' in required:
            from src.target_identifiers import canonical_target_identifier
            original_target = activity_input_target(SimpleNamespace(
                context=context, input_queries=[context.query]))
            for group in requirements.analysis_results:
                if group.tool_name == 'activity_predictor' and group.target is not None:
                    if original_target is None:
                        raise ValueError(_ERROR)
                    # The family resolver accepts surrounding text. Obligations
                    # require a whole supported identifier, not an embedded alias.
                    target_id = canonical_target_identifier(group.target)
                    if target_id is None or target_id != canonical_target_identifier(original_target):
                        raise ValueError(_ERROR)

        counts, subjects = set(), []
        for group in groups:
            if group.exact_molecule_count is not None:
                counts.add(group.exact_molecule_count)
            if group.expected_smiles:
                subjects.append(_canonical_subjects(group.expected_smiles, parser))
        if requirements.reverse_result is not None:
            subjects.append(_canonical_subjects((requirements.reverse_result.expected_smiles,), parser))
        if 'reverse_target_predictor' in required:
            counts.add(1)
        # Only explicit original input (or the server-selected whole structure)
        # can constrain these obligations. Missing input leaves them unchanged.
        selected = effective_molecule(context)
        if selected is not None:
            from src.agent.contracts.resolved_molecule import ResolvedScientificMolecule
            if type(selected) is not ResolvedScientificMolecule:
                raise ValueError(_ERROR)
            subjects.append(_canonical_subjects((selected.canonical_smiles,), parser))
        elif has_explicit_molecule(context.query):
            if 'activity_predictor' in required:
                from src.agent.tools.activity_input import parse_activity_input
                values = parse_activity_input(context.query, parser)[1]
            else:
                values = parse_molecular_smiles(context.query, parser)
            subjects.append(_canonical_subjects(values, parser))
        counts.update(len(subject) for subject in subjects)
        if len(counts) > 1 or subjects and any(subject != subjects[0] for subject in subjects):
            raise ValueError(_ERROR)
        return requirements
    except (TypeError, ValueError, RecursionError, ImportError):
        raise ValueError(_ERROR) from None
