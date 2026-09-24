"""Server-only initial admission for the bounded four-tool decision profile.

No route, planner, model call or tool execution. This conservative admission
boundary may ask for clarification; it must not promise unsupported work.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from src.agent.contracts import AgentContext
from src.agent.contracts.task_requirements import MolecularRequirement, TaskRequirements
from src.agent.contracts.target_request import analyze_target_request
from src.agent.contracts.generation_request import has_generation_intent, validate_generation_count
from src.agent.harness.decision_bounds import context_value, validate_json, configuration_generation
from src.agent.harness.decision_inputs import has_explicit_molecule
from src.agent.harness.decision_policy import DecisionBoundaryError
from src.agent.harness.decision_requirements import prepare_requirements
from src.agent.persistence.redaction import contains_secret_material


_FIELDS = frozenset({'type', 'message', 'enable_tools', 'enable_rag', 'mol_count',
    'rag_count', 'temperature', 'timestamp', 'client_id', 'reference', 'selection'})
_EXPLAIN = re.compile(r'^\s*(?:请)?(?:解释|介绍|什么是|讲解)|^\s*(?:please\s+)?'
    r'(?:explain|describe|define|what\s+(?:is|are))\b', re.I)
_UNSUPPORTED = re.compile(r'生成|设计|优化|筛选|排序|反向|寻靶|对接|文献|知识库|溶解度|毒性|'
    r'\b(?:generat\w*|design|optimi[sz]\w*|screen\w*|rank\w*|admet|reverse|rag|dock\w*|'
    r'solubility|toxicity|prepare_receptor|prepare_ligand|run_docking|get_docking_result)\b', re.I)
_QUALIFIED = re.compile(r'不要|不使用|不用|不能|排除|但是|但不要|改为|换成|'
    r'\b(?:except|exclude|without|instead|not|never)\b', re.I)
_METRICS = {
    'molecular_weight': r'分子量|\b(?:molecular\s+weight|mw)\b',
    'logp': r'\blogp\b', 'tpsa': r'\btpsa\b',
    'hbd': r'氢键供体|\bhbd\b', 'hba': r'氢键受体|\bhba\b', 'qed': r'\bqed\b',
}


class DecisionAdmissionError(ValueError):
    """Fixed public code only; never echoes user/provider/validation text."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PreparedDecision:
    _query: str
    _session_id: str
    _trace_id: str
    _enable_tools: bool
    _enable_rag: bool
    _temperature: float
    _mol_count: int
    _rag_count: int
    _resolved: object
    request_kind: str
    allowed_tools: frozenset[str]
    required_tools: frozenset[str]
    requirements: TaskRequirements
    config_generation: str | None

    @property
    def context(self):
        # No exposed mutable context aliases can change prepared obligations.
        return AgentContext(self._query, self._trace_id, user_id=self._session_id,
            session_id=self._session_id, temperature=self._temperature, mol_count=self._mol_count,
            metadata={'capabilities': {'scientific_tools': self._enable_tools, 'rag': self._enable_rag},
                      'rag_count': self._rag_count},
            resolved_molecule=replace(self._resolved) if self._resolved is not None else None)


def _classify(query):
    if has_generation_intent(query):
        raise DecisionAdmissionError('unsupported_scientific_request')
    explanation = _EXPLAIN.match(query)
    if explanation:
        clauses = [part.strip() for part in re.split(r'[\r\n;；。!?！？]+', query) if part.strip()]
        if len(clauses) > 1:
            # An explanation prefix has no authority over later statements,
            # including verbs outside the known scientific-action vocabulary.
            if all(_EXPLAIN.match(part) and _classify(part)[0] == 'chat' for part in clauses):
                return 'chat', frozenset(), ()
            raise DecisionAdmissionError('request_clarification_required')
        part = clauses[0]
        rest = part[_EXPLAIN.match(part).end():].strip()
        # Closed nominal topics, not free text after an explanation prefix.
        # Otherwise an unknown imperative needs no separator to bypass policy.
        topics = '|'.join(_METRICS.values()) + (
            r'|分子生成|分子对接|分子性质|分子属性|分子活性|类药性|靶点搜索'
            r'|药物(?:分子)?设计|\bRAG\b'
            r'|molecular\s+(?:generation|docking|properties|activity)|drug[ -]?likeness|target\s+search')
        topic = r'(?:' + topics + r')(?:\s*(?:的(?:概念|原理)|是什么))?'
        if re.fullmatch(topic + r'(?:\s*(?:和|及|、|\band\b)\s*' + topic + r'){0,7}', rest, re.I):
            return 'chat', frozenset(), ()
        raise DecisionAdmissionError('request_clarification_required')
    if _UNSUPPORTED.search(query):
        raise DecisionAdmissionError('unsupported_scientific_request')
    metrics = tuple(name for name, pattern in _METRICS.items() if re.search(pattern, query, re.I))
    tools = set()
    if re.search(r'性质|属性|理化|\bpropert(?:y|ies)\b', query, re.I) or metrics:
        tools.add('property_calculator')
    if re.search(r'类药|成药|lipinski|drug[ -]?likeness', query, re.I):
        tools.add('drug_likeness_assessment')
    if re.search(r'活性|\b(?:activity|potency|pic50|ic50)\b', query, re.I):
        tools.add('activity_predictor')
    if re.search(r'靶点|\btarget\b', query, re.I) and re.search(r'查询|搜索|查找|\b(?:search|find|lookup)\b', query, re.I):
        tools.add('target_database_search')
    if tools:
        if _QUALIFIED.search(query):
            raise DecisionAdmissionError('request_clarification_required')
        # Target lookup with a molecular calculation needs B's typed bindings;
        # A1 cannot silently infer which target/molecule each action consumes.
        if 'target_database_search' in tools and len(tools) > 1:
            raise DecisionAdmissionError('request_clarification_required')
        _require_complete_coverage(query, tools, metrics)
        return 'scientific', frozenset(tools), metrics
    if re.fullmatch(r'\s*(?:你好|您好|谢谢|再见|hi|hello|thanks|thank\s+you)[!！。.\s]*', query, re.I):
        return 'chat', frozenset(), ()
    # A1 has no reliable general intent classifier. Unknown does not mean chat.
    raise DecisionAdmissionError('request_clarification_required')


def _subjects(query, *, activity):
    from src.agent.tools.base_tool import BaseMolecularTool
    from src.agent.tools.molecular_input import parse_molecular_smiles
    from src.agent.tools.activity_input import activity_target, parse_activity_input
    parser = BaseMolecularTool('admission', '')
    if activity:
        # Unknown/conflicting explicit targets fail even if a molecule is absent.
        activity_target(query, parser.validate_smiles)
    if not has_explicit_molecule(query):
        return ()
    values = parse_activity_input(query, parser)[1] if activity else parse_molecular_smiles(query, parser)
    return tuple(values)  # Preserve spelling/order, not canonicalized fragments.


def _require_complete_coverage(query, tools, metrics):
    """Consume the whole supported request, not just recognized result nouns.

    This is a closed, conservative surface for the existing four obligations,
    not an NLP engine or an execution grammar. Any unconsumed text clarifies:
    unknown endpoints, negatives and quantified prose need no deny vocabulary.
    Only existing parsers may supply structure/target terminals. Nothing here
    repairs, drops or changes the query sent to the real tools.
    """
    from src.agent.tools.molecular_input import _MARKER
    from src.target_identifiers import TARGET_PATTERN

    subjects = () if 'target_database_search' in tools else _subjects(
        query, activity='activity_predictor' in tools)
    # Case-sensitive, whole validated subjects; never match a chemical fragment
    # inside a word or substitute a resolved molecule into the user's prose.
    structure = re.compile(r'(?<![A-Za-z0-9_])(?:' + '|'.join(
        re.escape(s) for s in sorted(set(subjects), key=len, reverse=True))
        + r')(?![A-Za-z0-9_])') if subjects else None
    obligations = {
        'property_calculator': r'理化性质|性质|属性|\bpropert(?:y|ies)\b',
        'drug_likeness_assessment': r'类药性|成药性|\b(?:lipinski|drug[ -]?likeness)\b',
        'activity_predictor': r'活性|\b(?:activity|potency|pic50|ic50)\b',
        'target_database_search': r'靶点结构|靶点|结构|\b(?:target|structures?)\b',
    }
    tokens = [*(_METRICS[m] for m in metrics), *(obligations[t] for t in sorted(tools)),
        r'计算|预测|评估|分析|查询|搜索|查找|\b(?:calculate|compute|predict|assess|evaluate|analy[sz]e|search|find|lookup)\b',
        # Only singular reference ordinals; exact range/ACK/owner checks remain
        # in ScientificReferenceService. Counts/quantifiers are not terminals.
        r'第(?:[一二三四五六七八九十]{1,3}|[1-9][0-9]?)个',
        r'刚才|上一个|这个|该|分子|化合物|候选',
        r'\b(?:previous|selected|first|second|third|molecules?|compounds?|candidates?)\b',
        r'请|帮我|包含|然后|针对|的|和|及|与|并|对',
        r'\b(?:please|molecular|the|of|for|and|then|with|including|against)\b',
        r'\s+|[，,;；、。!?！？()（）\x22\x27`]',
    ]
    syntax = re.compile('|'.join('(?:' + token + ')' for token in tokens), re.I)
    position = 0
    while position < len(query):
        match = structure.match(query, position) if structure is not None else None
        if match is None and subjects:
            match = _MARKER.match(query, position)
        if match is None and tools & {'activity_predictor', 'target_database_search'}:
            match = TARGET_PATTERN.match(query, position)
        match = match or syntax.match(query, position)
        if match is None:
            raise DecisionAdmissionError('request_clarification_required')
        position = match.end()


def prepare_decision_request(payload, *, session_id, trace_id, references=None, config_generation=None):
    """Prepare a detached initial request. Identity/config/service are server args.

    Browser reference/selection are only hints to the existing owner/ACK service.
    Missing molecule/target may be clarified by the later model loop; unsupported
    intent is rejected here as a whole request, never partially reclassified chat.
    """
    try:
        validate_json(payload, max_bytes=24 * 1024, reason='invalid_request')
        if type(payload) is not dict:
            raise DecisionAdmissionError('invalid_request')
        if set(payload) - _FIELDS:
            raise DecisionAdmissionError('untrusted_request_field')
        if any(type(v) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', v)
               for v in (session_id, trace_id)):
            raise DecisionAdmissionError('missing_server_identity')
        generation = configuration_generation(config_generation)
        query = payload.get('message')
        if type(query) is not str or not query.strip() or payload.get('type', 'chat') != 'chat':
            raise DecisionAdmissionError('invalid_request')
        validate_json(query, max_bytes=16 * 1024, reason='invalid_request')
        if contains_secret_material(payload):
            raise DecisionAdmissionError('sensitive_input_rejected')
        enable_tools, enable_rag = payload.get('enable_tools', True), payload.get('enable_rag', True)
        if type(enable_tools) is not bool or type(enable_rag) is not bool:
            raise DecisionAdmissionError('invalid_options')
        mol_count, rag_count = payload.get('mol_count'), payload.get('rag_count', 5)
        mol_count = 5 if mol_count is None else validate_generation_count(mol_count, field='mol_count')
        if type(rag_count) is not int or not 1 <= rag_count <= 20:
            raise DecisionAdmissionError('invalid_options')
        temperature = payload.get('temperature', 0.7)
        if type(temperature) not in (int, float) or not 0 <= temperature <= 2:
            raise DecisionAdmissionError('invalid_options')
        kind, required, metrics = _classify(query)
        if kind == 'scientific' and not enable_tools:
            raise DecisionAdmissionError('scientific_tools_disabled')
        subjects, selected = (), None
        if kind == 'scientific':
            if 'target_database_search' in required:
                if analyze_target_request(query).needs_clarification:
                    raise DecisionAdmissionError('request_clarification_required')
            else:
                subjects = _subjects(query, activity='activity_predictor' in required)
                if not subjects:
                    if references is not None:
                        selected = references.resolve(query, payload.get('reference'), payload.get('selection'),
                            session_id=session_id, enable_tools=enable_tools)
                        projected = context_value(AgentContext(query, trace_id, resolved_molecule=selected))
                        if contains_secret_material(projected):
                            raise DecisionAdmissionError('sensitive_input_rejected')
                    elif payload.get('reference') is not None or payload.get('selection') is not None:
                        raise DecisionAdmissionError('scientific_reference_unavailable')
                    if selected is not None:
                        subjects = (selected.canonical_smiles,)
        obligations = tuple(MolecularRequirement(tool_name=name, expected_smiles=subjects,
            exact_molecule_count=len(subjects) or None,
            required_metrics=metrics if name == 'property_calculator' else ('lipinski_compliant',))
            for name in sorted(required & {'property_calculator', 'drug_likeness_assessment'}))
        requirements = prepare_requirements(TaskRequirements(molecular_results=obligations),
            request_kind=kind, allowed_tools=required, required_tools=required)
        return PreparedDecision(query, session_id, trace_id, enable_tools, enable_rag, temperature,
            mol_count, rag_count, selected, kind, required, required, requirements, generation)
    except DecisionAdmissionError:
        raise
    except (DecisionBoundaryError, ValueError, TypeError, ImportError):
        raise DecisionAdmissionError('request_clarification_required') from None
