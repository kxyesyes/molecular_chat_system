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
_ACTION = re.compile(r'计算|预测|评估|分析|查询|搜索|查找|检索|生成|设计|优化|筛选|排序|对接|运行|执行|'
    r'\b(?:calculate|compute|predict|assess|evaluate|analy[sz]e|find|search|retrieve|generate|design|'
    r'optimi[sz]e|screen|rank|dock|run|execute)\b', re.I)
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
        rest = query[explanation.end():]
        # Topic nouns are not actions. Mixed explain+execute must not escape
        # scientific admission through an explanation prefix.
        if not (re.search(r'然后|再|同时|并|[,，;；。!?！？]|\b(?:then|and)\b', rest, re.I)
                and _ACTION.search(rest)) and not re.search(
                    r'\b(?:calculate|compute|predict|generate|run|execute)\b', rest, re.I):
            return 'chat', frozenset(), ()
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
    if tools or _ACTION.search(query):
        if not tools or _QUALIFIED.search(query):
            raise DecisionAdmissionError('request_clarification_required')
        # Every execution clause must name a supported obligation. A recognized
        # first clause cannot hide an unknown second action. This is admission,
        # not an ordered plan or a model/tool selector.
        supported = r'性质|属性|理化|类药|成药|活性|靶点|\b(?:properties|property|activity|potency|target|lipinski|drug[ -]?likeness|pic50|ic50)\b'
        actions = list(_ACTION.finditer(query))
        for index, action in enumerate(actions):
            tail = query[action.end():actions[index + 1].start() if index + 1 < len(actions) else len(query)]
            if (action.group().casefold() in {'运行', '执行', 'run', 'execute'}
                    or not re.search(supported + '|' + '|'.join(_METRICS.values()), tail, re.I)):
                raise DecisionAdmissionError('request_clarification_required')
        # A1 derives exact count from explicit subjects. Quantified prose needs
        # a separate reviewed count grammar; do not silently discard its demand.
        if re.search(r'(?<![第\d零一二两三四五六七八九十百])(?:\d+|[零一二两三四五六七八九十百]+)\s*个\s*(?:分子|化合物)|'
                     r'\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:molecules|compounds)\b', query, re.I):
            raise DecisionAdmissionError('request_clarification_required')
        # Target lookup with a molecular calculation needs B's typed bindings;
        # A1 cannot silently infer which target/molecule each action consumes.
        if 'target_database_search' in tools and len(tools) > 1:
            raise DecisionAdmissionError('request_clarification_required')
        return 'scientific', frozenset(tools), metrics
    return 'chat', frozenset(), ()


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
