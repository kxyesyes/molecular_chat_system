"""Server-only initial admission for the bounded four-tool decision profile.

No route, planner, model call or tool execution. This conservative admission
boundary may ask for clarification; it must not promise unsupported work.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, fields, replace

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
class ValidatedRequest:
    """Detached server envelope; JSON hints have no reference/owner authority."""
    query: str
    session_id: str
    trace_id: str
    enable_tools: bool
    enable_rag: bool
    temperature: float
    mol_count: int
    rag_count: int
    config_generation: str | None
    _reference_json: str
    _selection_json: str

    @property
    def reference(self):
        return json.loads(self._reference_json)

    @property
    def selection(self):
        return json.loads(self._selection_json)


@dataclass(frozen=True)
class WholeRequestAssessment:
    """Recomputed server record, not a signature or model-authored obligations."""
    version: str
    kind: str
    reason: str
    query_digest: str

    def __post_init__(self):
        reasons = {
            'known_scientific': {'supported_scientific_request'},
            'known_chat': {'closed_chat_request'},
            'semantic_candidate': {'intent_required'},
            'blocked': {'request_clarification_required', 'unsupported_scientific_request',
                        'scientific_tools_disabled'},
        }
        if (self.version != '1' or type(self.kind) is not str or self.kind not in reasons
                or type(self.reason) is not str or self.reason not in reasons[self.kind]
                or type(self.query_digest) is not str
                or not re.fullmatch(r'[a-f0-9]{64}', self.query_digest)):
            raise DecisionAdmissionError('request_clarification_required')


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
        'property_calculator': r'分子性质|分子属性|理化性质|性质|属性|\bpropert(?:y|ies)\b',
        'drug_likeness_assessment': r'类药性|成药性|\b(?:lipinski|drug[ -]?likeness)\b',
        'activity_predictor': r'分子活性|活性|\b(?:activity|potency|pic50|ic50)\b',
        'target_database_search': r'靶点结构|靶点|结构|\b(?:target|structures?)\b',
    }
    result_tokens = [*(_METRICS[m] for m in metrics), *(obligations[t] for t in sorted(tools))]
    ordinal = r'第(?:[一二三四五六七八九十]{1,3}|[1-9][0-9]?)个'
    tokens = [
        '(?P<obligation>' + '|'.join(result_tokens) + ')',
        r'(?P<action>计算|预测|评估|分析|查询|搜索|查找|\b(?:calculate|compute|predict|assess|evaluate|analy[sz]e|search|find|lookup)\b)',
        # All already-supported referring modifiers have a subject role.
        # Preserve the existing adjacent temporal+ordinal single reference;
        # range/ACK/owner checks still belong to ScientificReferenceService.
        r'(?P<reference>刚才(?:\s*' + ordinal + r')?|' + ordinal
        + r'|上一个|这个|该|\b(?:first|second|third|previous|selected)\b)',
        r'(?P<subject>候选(?:分子|化合物)?|分子|化合物|\b(?:candidate(?:\s+(?:molecule|compound))?|molecule|compound)\b)',
        r'(?P<plural>\b(?:molecules|compounds|candidates)\b)',
        r'(?P<join>然后|和|及|与|并|\b(?:and|then)\b)',
        r'(?P<include>包含|\bincluding\b)',
        r'(?P<relation>针对|的|对|\b(?:of|for|with|against)\b)',
        r'(?P<article>\b(?:molecular|the)\b)',
        r'(?P<polite>请|帮我|\bplease\b)',
        r'(?P<separator>[，,;；、\r\n]+)',
        r'(?P<end>[。!?！？])',
        r'(?P<space>[^\S\r\n]+|[()（）\x22\x27`])',
    ]
    syntax = re.compile('|'.join('(?:' + token + ')' for token in tokens), re.I)
    position = 0
    seen_action = seen_obligation = False
    stream, consumed_structures = [], []
    while position < len(query):
        match = structure.match(query, position) if structure is not None else None
        role = 'explicit' if match else None
        if match:
            consumed_structures.append(match.group())
        if match is None and subjects:
            match = _MARKER.match(query, position)
            role = 'field' if match else None
        if match is None and tools & {'activity_predictor', 'target_database_search'}:
            match = TARGET_PATTERN.match(query, position)
            role = 'target' if match else None
        if match is None:
            match = syntax.match(query, position)
            role = match.lastgroup if match else None
        if match is None:
            raise DecisionAdmissionError('request_clarification_required')
        if role == 'action':
            if seen_action or seen_obligation:
                raise DecisionAdmissionError('request_clarification_required')
            seen_action = True
        elif role == 'polite':
            if stream:
                raise DecisionAdmissionError('request_clarification_required')
        elif role != 'space':
            seen_obligation |= role == 'obligation'
            stream.append((role, match.group()))
        position = match.end()
    if not seen_obligation or tuple(consumed_structures) != subjects:
        raise DecisionAdmissionError('request_clarification_required')
    _reduce_input_phrases(stream, subjects)


def _reduce_input_phrases(stream, subjects):
    """Reduce the complete request to one input set; never reset at a metric.

    Three subject states: generic slot, confirmed-reference phrase, explicit
    set. Connections and declarations remain in the stream until ownership is
    checked globally. This validates admission only; it cannot execute a plan.
    """
    nominal = {'subject', 'plural'}
    subject_atoms = nominal | {'reference', 'explicit'}
    atoms = subject_atoms | {'obligation', 'target'}
    # Articles must introduce a real atom, not hide a dangling connection.
    tokens, following = [], None
    for token in reversed(stream):
        if token[0] == 'article':
            if following not in atoms:
                raise DecisionAdmissionError('request_clarification_required')
        else:
            tokens.append(token)
            following = token[0]
    tokens.reverse()
    nodes, index, first_field = [], 0, None
    while index < len(tokens):
        role, value = tokens[index]
        if role == 'field':
            if index + 1 == len(tokens) or tokens[index + 1][0] != 'explicit':
                raise DecisionAdmissionError('request_clarification_required')
            first_field = len(nodes) if first_field is None else first_field
            index += 1
            continue
        if role not in subject_atoms:
            nodes.append((role, value))
            index += 1
            continue
        state, described, members = None, False, []
        while index < len(tokens):
            role, value = tokens[index]
            if (state == 'reference' and not described and value == '的'
                    and index + 1 < len(tokens) and tokens[index + 1][0] in nominal):
                index += 1
                continue
            if role not in subject_atoms:
                break
            if role in nominal:
                if described or (role == 'plural' and len(subjects) < 2):
                    raise DecisionAdmissionError('request_clarification_required')
                described = True
                state = state or 'generic'
            elif role == 'reference':
                if state is not None or subjects:
                    raise DecisionAdmissionError('request_clarification_required')
                state = 'reference'
            else:
                if state == 'reference':
                    raise DecisionAdmissionError('request_clarification_required')
                state = 'explicit'
                members.append(value)
            index += 1
        nodes.append((state, tuple(members) if state == 'explicit' else ''))
    inputs = [(i, kind) for i, (kind, _) in enumerate(nodes)
              if kind in {'generic', 'reference', 'explicit'}]
    generic = [i for i, kind in inputs if kind == 'generic']
    if any(kind == 'reference' for _, kind in inputs):
        if len(inputs) != 1:
            raise DecisionAdmissionError('request_clarification_required')
    elif subjects and generic:
        # Only a declaration can fill a separated generic slot. A later
        # explicit phrase across metrics/punctuation is not implicit binding.
        if (len(generic) != 1 or first_field is None or generic[0] >= first_field
                or any(i < first_field for i, kind in inputs if kind == 'explicit')):
            raise DecisionAdmissionError('request_clarification_required')
    elif len(generic) > 1:
        raise DecisionAdmissionError('request_clarification_required')
    _require_phrase_connections(nodes)
    _require_shared_explicit_scope(nodes, subjects)


def _require_shared_explicit_scope(nodes, subjects):
    """Bind every obligation to one whole position-identified explicit group.

    Mere union coverage is insufficient: an obligation between explicit groups
    is a local/ambiguous scope, not permission to apply all tools to their union.
    """
    if not subjects:
        return  # Existing reference/generic-slot validation stays authoritative.
    groups, current = [], None
    for index, (kind, members) in enumerate(nodes):
        if kind == 'explicit':
            if current is None:
                current = [index, index, members]
                groups.append(current)
            else:
                current[1] = index
                current[2] += members
        elif kind not in {'join', 'separator', 'end'}:
            current = None  # In particular, never merge across an obligation.
    if len(groups) != 1 or groups[0][2] != subjects:
        raise DecisionAdmissionError('request_clarification_required')
    start, end, _ = groups[0]
    # Obligations on either side share this exact group, never a nearest subset.
    if any(start <= i <= end for i, (kind, _) in enumerate(nodes) if kind == 'obligation'):
        raise DecisionAdmissionError('request_clarification_required')


def _require_phrase_connections(nodes):
    """Connections relate complete phrases, not the nearest lexical token."""
    atoms = {'generic', 'reference', 'explicit', 'obligation', 'target'}
    previous, links = None, []
    for kind, value in nodes:
        if kind not in atoms:
            links.append((kind, value))
            continue
        words = [(role, word) for role, word in links if role not in {'separator', 'end'}]
        pair = (previous, kind)
        if previous is None:
            if words and not (words in [[('relation', '对')], [('relation', '针对')]]
                              and kind in atoms - {'obligation'}):
                raise DecisionAdmissionError('request_clarification_required')
        elif words:
            if len(words) != 1:
                raise DecisionAdmissionError('request_clarification_required')
            role, _ = words[0]
            if role in {'join', 'include'}:
                valid = pair == ('obligation', 'obligation') or (
                    role == 'join' and pair == ('explicit', 'explicit'))
            else:
                valid = role == 'relation' and (
                    (previous == 'obligation' and kind != 'obligation')
                    or (kind == 'obligation' and previous != 'obligation'))
            if not valid:
                raise DecisionAdmissionError('request_clarification_required')
        previous, links = kind, []
    if any(kind != 'end' and not (kind == 'separator' and value.isspace())
           for kind, value in links):
        raise DecisionAdmissionError('request_clarification_required')


def prepare_decision_request(payload, *, session_id, trace_id, references=None, config_generation=None):
    """Prepare a detached initial request. Identity/config/service are server args.

    Browser reference/selection are only hints to the existing owner/ACK service.
    Missing molecule/target may be clarified by the later model loop; unsupported
    intent is rejected here as a whole request, never partially reclassified chat.
    """
    envelope = validate_request_envelope(payload, session_id=session_id, trace_id=trace_id,
        config_generation=config_generation)
    return _prepare_validated_science(envelope, references=references)


def validate_request_envelope(payload, *, session_id, trace_id, config_generation=None):
    """Original validation and order, before any semantic or scientific analysis."""
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
        return ValidatedRequest(query, session_id, trace_id, enable_tools, enable_rag,
            temperature, mol_count, rag_count, generation,
            json.dumps(payload.get('reference'), ensure_ascii=False, allow_nan=False),
            json.dumps(payload.get('selection'), ensure_ascii=False, allow_nan=False))
    except DecisionAdmissionError:
        raise
    except (DecisionBoundaryError, ValueError, TypeError, ImportError):
        raise DecisionAdmissionError('request_clarification_required') from None


def _prepare_validated_science(envelope, *, references=None):
    """Original classifier and preparation tail; never accept proposed tool sets."""
    query, session_id, trace_id = envelope.query, envelope.session_id, envelope.trace_id
    enable_tools, enable_rag = envelope.enable_tools, envelope.enable_rag
    temperature, mol_count, rag_count = envelope.temperature, envelope.mol_count, envelope.rag_count
    generation = envelope.config_generation
    payload = {'reference': envelope.reference, 'selection': envelope.selection}
    try:
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


# Version the bounded lexical policy separately from the immutable record shape.
# This is a conservative proposal gate, NOT a universal natural-language proof.
# Unrecognized indirect obligations remain a residual risk requiring later gates.
ASSESSMENT_REVISION = 'whole-request-v1'
_NOMINAL_RISK = (
    r'分子(?:生成|对接|设计|优化|筛选|性质|属性|活性)|毒性预测|活性预测|靶点搜索|'
    r'(?:科研|科学)计算|知识库|数据库|文献|检索|类药性|成药性|溶解度|毒性|熔点|亲和力|'
    r'结合能|对接能|分子量|氢键供体|氢键受体|性质|属性|活性|'
    r'logp|tpsa|hbd|hba|qed|pic50|ic50|admet|'
    r'\b(?:molecular\s+(?:generation|docking|design|optimization|properties|activity|weight)|'
    r'drug[ -]?likeness|target\s+search|toxicity\s+prediction|scientific\s+computing|'
    r'retrieval|docking|solubility|toxicity|affinity|properties|potency|activity|mw|rag)\b'
)
_ACTION_RISK = (
    r'计算|预测|测量|测定|生成|优化|设计|对接|检索|搜索|查询|查找|运行|执行|调用|'
    r'输出|给出|提供|筛选|排序|读取|下载|上传|保存|写入|删除|评估|分析|寻靶|反向|'
    r'\b(?:compute|calculate|predict|measure|generate|optimi[sz]e|dock|retrieve|search|run|'
    r'execute|invoke|call|give|provide|find|lookup|screen|rank|design|assess|evaluate|'
    r'analy[sz]e|read|download|upload|save|write|delete|set)\b'
)
_RISK_SCAN = re.compile(
    r'(?P<boundary>[\r\n;；。!?！？,，]+|\.(?=\s|$))|'
    r'(?P<evidence>检索结果|数据库检索|引用|来源|参考文献|数值|结果|是多少|为多少|'
    r'\b(?:citations?|sources?|references?|doi|pubmed|results?|values?|binding\s+energy|'
    r'melting\s+point)\b)|'
    r'(?P<asset>smiles|box|文件|代码|脚本|工具|\b(?:file|files|code|script|scripts|tools?|'
    r'python|bash|powershell|curl)\b|\b[a-z][a-z0-9_]{0,63}\s{0,8}\(|```|'
    r'\.(?:sdf|pdb|csv|py|sh|exe)\b|[a-z]:[/\\]|[/\\][a-z]|\b[cnops]{3,}\b)|'
    r'(?P<nominal>' + _NOMINAL_RISK + r')|(?P<action>' + _ACTION_RISK + r')')
# Complete nominal descriptions, not an allowlist of prompts or a prefix grant.
# Bound coordination to eight topics. Unconsumed text (including a noun used as
# a command, quantities, subject arguments, or a new imperative) is ambiguous.
_TOPIC_LIST = (r'(?:' + _NOMINAL_RISK + r')(?:\s*(?:和|与|及|、|\band\b|\bor\b|&)'
               r'\s*(?:' + _NOMINAL_RISK + r')){0,7}')
_DESCRIPTION_ONLY = re.compile(
    r'(?:(?:请)?(?:解释|介绍|讲解|什么是|聊聊)|'
    r'(?:我)?(?:只是)?(?:想|希望)(?:了解|理解|听听))\s*(?:一下|有关|关于)?\s*'
    + _TOPIC_LIST + r'(?:的(?:基本|一般)?(?:概念|原理|含义|定义|用途|区别)|是什么)?|'
    r'(?:(?:(?:please|could\s+you|can\s+you)\s+)?(?:explain|describe|define)\s+'
    r'(?:what\s+)?|what\s+(?:is|are)\s+)' + _TOPIC_LIST
    + r'(?:\s+means)?(?:\s+in\s+(?:simple|plain|general)\s+(?:terms|language))?')
_CAPABILITY_TOPICS = (r'(?:' + _NOMINAL_RISK
    + r'|生成分子|对接分子|计算分子性质|预测毒性|预测活性|'
      r'generate\s+molecules|(?:compute|calculate)\s+molecular\s+properties)')
_CAPABILITY_ONLY = re.compile(
    r'(?:这个|该)?(?:系统|平台)(?:能|可以|支持)' + _CAPABILITY_TOPICS
    + r'(?:\s*(?:和|与|及|、)\s*' + _CAPABILITY_TOPICS + r'){0,7}(?:吗)?|'
    r'(?:what\s+can\s+(?:this|the)\s+(?:system|platform)\s+do\s+for|'
    r'(?:can|does)\s+(?:this|the)\s+(?:system|platform)(?:\s+support)?)\s+'
    + _CAPABILITY_TOPICS + r'(?:\s+(?:and|or)\s+' + _CAPABILITY_TOPICS + r'){0,7}')
_PROHIBITION = re.compile(
    r'^(?:请)?(?:不要|不用|不使用|禁止)(?:调用|使用|运行|执行)?(?:任何)?'
    r'(?:(?:科研|科学|计算|预测|生成|对接|检索|分子|工具|模型)|\s){1,24}$|'
    r'^(?:please\s+)?(?:do\s+not|don\x27t|never)\s+(?:use|call|run|invoke|execute)\s+'
    r'(?:(?:any|scientific|computation|calculation|compute|research|tools?|models?)\s*){1,12}$')


def _validated_envelope(envelope):
    """Recheck server records too; frozen values are not cryptographic provenance."""
    if (type(envelope) is not ValidatedRequest
            or set(vars(envelope)) != {field.name for field in fields(ValidatedRequest)}):
        raise DecisionAdmissionError('invalid_request')
    for raw in (envelope._reference_json, envelope._selection_json):
        if type(raw) is not str or len(raw) > 24 * 1024:
            raise DecisionAdmissionError('invalid_request')
    try:
        # Do not charge omitted defaults against the original 24KiB allowance.
        # Exact types matter: e.g. 5.0/True must not disappear as an int default.
        payload = {'message': envelope.query}
        for name, default in (('enable_tools', True), ('enable_rag', True),
                              ('temperature', 0.7), ('mol_count', 5), ('rag_count', 5)):
            value = getattr(envelope, name)
            if type(value) is not type(default) or value != default:
                payload[name] = value
        for name, value in (('reference', envelope.reference), ('selection', envelope.selection)):
            if value is not None:
                payload[name] = value
        checked = validate_request_envelope(payload,
            session_id=envelope.session_id, trace_id=envelope.trace_id,
            config_generation=envelope.config_generation)
        if checked != envelope:
            raise DecisionAdmissionError('invalid_request')
        return checked
    except (ValueError, TypeError, RecursionError):
        raise DecisionAdmissionError('invalid_request') from None


def _whole_risk(query):
    """One full linear token scan; at most 128 independently scoped clauses.

    Nominal mentions are safe only in descriptive scope. Negative-only commands
    match an entire narrow tool-prohibition grammar, never text deletion. An
    unseparated action/asset/result suffix still has its own risk token. No token
    or intent supplies executable obligations; the old whole-coverage parser does.
    """
    view = unicodedata.normalize('NFKC', query).casefold()
    clauses, start, roles = [], 0, set()
    overflow = False
    for token in _RISK_SCAN.finditer(view):
        role = token.lastgroup
        if role == 'boundary':
            if len(clauses) < 128:
                clauses.append((view[start:token.start()].strip(), roles))
            else:
                overflow = True
            start, roles = token.end(), set()
        else:
            roles.add(role)
    if overflow or len(clauses) >= 128:
        return 'ambiguous'
    clauses.append((view[start:].strip(), roles))
    demand = negative = False
    for clause, risks in clauses:
        if not clause:
            continue
        if _PROHIBITION.fullmatch(clause):
            negative = True
            continue
        if not risks:
            continue
        if ((risks <= {'nominal'} and _DESCRIPTION_ONLY.fullmatch(clause))
                or (risks <= {'nominal', 'action'} and _CAPABILITY_ONLY.fullmatch(clause))):
            continue
        demand = True
    return 'ambiguous' if demand and negative else 'demand' if demand else 'clear'


def assess_whole_request(envelope):
    """Only known complete science/chat bypass intent; a candidate is NOT chat."""
    from src.agent.evidence.ledger import EvidenceLedger
    envelope = _validated_envelope(envelope)
    digest = EvidenceLedger.output_digest(envelope.query)
    risk = _whole_risk(envelope.query)
    def result(kind, reason):
        return WholeRequestAssessment('1', kind, reason, digest)
    if risk == 'ambiguous':
        return result('blocked', 'request_clarification_required')
    try:
        kind, required, _ = _classify(envelope.query)
        if kind == 'scientific' and 'target_database_search' in required:
            if analyze_target_request(envelope.query).needs_clarification:
                return result('blocked', 'request_clarification_required')
    except (DecisionAdmissionError, DecisionBoundaryError, ValueError, TypeError, ImportError) as exc:
        if risk != 'clear':
            code = getattr(exc, 'code', None)
            return result('blocked', code if code == 'unsupported_scientific_request'
                          else 'request_clarification_required')
        # Eligibility was independently checked above, NOT inferred from failure.
        return result('semantic_candidate', 'intent_required')
    if kind == 'scientific':
        if not envelope.enable_tools:
            return result('blocked', 'scientific_tools_disabled')
        return result('known_scientific', 'supported_scientific_request')
    if risk != 'clear':
        return result('blocked', 'request_clarification_required')
    return result('known_chat', 'closed_chat_request')


def _current_capability_snapshot(value, envelope):
    from src.agent.contracts.ordinary_admission import (
        CapabilitySnapshot, capability_digest, parse_capability_snapshot,
    )
    from src.web.ordinary_capabilities import CATALOG_REVISION, PROFILE_REVISION, PRODUCT_CATALOG
    if type(value) is not CapabilitySnapshot:
        raise DecisionAdmissionError('ordinary_capabilities_unavailable')
    # Strict Task3 validation precedes serialization; no attribute discovery/hooks.
    capability_digest(value)
    current = parse_capability_snapshot(value.model_dump_json())
    if (current.profile_revision != PROFILE_REVISION or current.catalog_revision != CATALOG_REVISION
            or current.model_generation != envelope.config_generation
            or tuple((f.id, f.product_description) for f in current.features) != PRODUCT_CATALOG
            or any(f.readiness != 'unknown' for f in current.features)):
        raise DecisionAdmissionError('ordinary_capabilities_unavailable')
    return current


def _ordinary_prepared(envelope):
    empty = frozenset()
    requirements = prepare_requirements(TaskRequirements(), request_kind='chat',
        allowed_tools=empty, required_tools=empty)
    return PreparedDecision(envelope.query, envelope.session_id, envelope.trace_id,
        envelope.enable_tools, envelope.enable_rag, envelope.temperature, envelope.mol_count,
        envelope.rag_count, None, 'chat', empty, empty, requirements, envelope.config_generation)


def prepare_with_intent(envelope, assessment, intent, *, history, capability_snapshot,
                        intent_requests, references=None):
    """Use an untrusted proposal only after server whole-query/history/view checks.

    history is already eligible ordinary history captured by the server caller,
    not browser or model history. This layer validates its established shape and
    binds it; Web must capture prepared.context once and assign that frozen copy.
    No model calls, reference resolution for chat, or context.memory writes here.
    """
    from src.agent.contracts.ordinary_intent import OrdinaryIntent
    from src.agent.contracts.ordinary_admission import build_admission_binding
    from src.agent.harness.decision_history import history_pairs
    try:
        envelope = _validated_envelope(envelope)
        fresh = assess_whole_request(envelope)
        if (type(assessment) is not WholeRequestAssessment
                or set(vars(assessment)) != {field.name for field in fields(WholeRequestAssessment)}
                or assessment != fresh):
            raise DecisionAdmissionError('request_clarification_required')
        if fresh.kind == 'blocked':
            raise DecisionAdmissionError(fresh.reason)
        # Task3's 32KiB checksum limit is NOT the existing 20-pair/16KiB policy.
        kept = history_pairs(history)
        current = _current_capability_snapshot(capability_snapshot, envelope)
        if type(intent_requests) is not int:
            raise DecisionAdmissionError('request_clarification_required')
        if fresh.kind in {'known_chat', 'known_scientific'}:
            if intent is not None or intent_requests != 0:
                raise DecisionAdmissionError('request_clarification_required')
            prepared = _prepare_validated_science(envelope, references=references)
            intent_kind = fresh.kind
        else:
            if (intent_requests != 1 or type(intent) is not OrdinaryIntent
                    or set(vars(intent)) != {'version', 'kind', 'history_relation', 'unresolved'}
                    or intent.model_extra):
                raise DecisionAdmissionError('request_clarification_required')
            # Task1 does not revalidate instances: validate the exact plain fields,
            # including constructed/copied instances, with its strict schema.
            proposal = OrdinaryIntent.model_validate(dict(vars(intent)), strict=True)
            if (proposal.unresolved or proposal.kind in {'mixed', 'uncertain'}
                    or (proposal.history_relation == 'prior_ordinary_turn' and not kept)):
                raise DecisionAdmissionError('request_clarification_required')
            intent_kind = proposal.kind
            if intent_kind in {'scientific_execution', 'retrieval'}:
                prepared = _prepare_validated_science(envelope, references=references)
                if prepared.request_kind != 'scientific':
                    raise DecisionAdmissionError('request_clarification_required')
            else:
                feature = current.features[0]
                if not feature.wired or not feature.permitted:
                    raise DecisionAdmissionError('ordinary_capabilities_unavailable')
                prepared = _ordinary_prepared(envelope)
        binding = build_admission_binding(current, query=envelope.query, history=kept,
            assessment_revision=ASSESSMENT_REVISION, intent_kind=intent_kind,
            intent_requests=intent_requests)
        return prepared, json.dumps(binding, ensure_ascii=False, allow_nan=False)
    except DecisionAdmissionError:
        raise
    except (DecisionBoundaryError, ValueError, TypeError, ImportError, RecursionError):
        raise DecisionAdmissionError('request_clarification_required') from None
