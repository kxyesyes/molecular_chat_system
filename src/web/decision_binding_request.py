"""B-only whole-request reduction to existing v2 obligations, never an executor.

Every scientific clause must be consumed, with one shared molecular input scope.
Typed target/retrieval phrases cannot donate molecular or target authority to
another obligation. Intent proposals and product descriptions authorize no work.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import replace

from src.agent.contracts import AgentContext
from src.agent.contracts.binding_requirements import (
    ActivityRequirement, AdmetRequirement, BindingRequirements, ReverseRequirement,
    RetrievalRequirement, TargetRequirement,
)
from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION
from src.agent.contracts.task_requirements import MolecularRequirement
from src.agent.harness.decision_bindings import prepare_binding_requirements
from src.agent.harness.decision_bounds import context_value
from src.agent.harness.decision_policy import DecisionBoundaryError
from src.agent.persistence.redaction import contains_secret_material


ASSESSMENT_REVISION = 'whole-request-b-v1'
RAG_COUNT_NOTICE = '本入口仅支持检索数量 3；请明确选择 3，不会自动更改已请求的数量。'
_ADMET = r'(?:(?:现有|可用)方法(?:的)?\s*)?\bADME(?:T)?\b(?:\s*(?:可用|现有)方法)?'
_REVERSE = r'反向寻靶|反向靶点预测|\breverse\s+target\s+(?:prediction|search)\b'
_LINKED = (r'(?:' + _REVERSE + r')\s*(?:并|然后|\band\b)\s*'
    r'(?:查询返回的一个靶点|lookup\s+one\s+returned\s+target)')
_B_ANALYSIS = re.compile(_ADMET + '|' + _REVERSE, re.I)
_RETRIEVAL = re.compile(
    r'\s*(?:请\s*)?(?:使用\s*RAG\s*)?检索本地分子库'
    r'(?:中与(?P<topic>[^；;。！？!?\r\n，,]{1,128})相关的分子)?'
    r'(?P<hits>并要求有命中)?[。！!]?\s*', re.I)
_PROSE_COUNT = re.compile(r'[，,；;]\s*返回(?:[0-9]+|[一二三四五六七八九十]+)个[。！!]?\s*$')
_B_DESCRIPTION = re.compile(
    r'\s*(?:请)?(?:解释|介绍|讲解|什么是)\s*(?:' + _ADMET + '|' + _REVERSE + r')\s*'
    r'(?:的\s*(?:基本|一般)?(?:概念|原理|含义|定义|用途))?[。！？!?]?\s*|'
    r'\s*(?:please\s+)?(?:explain|describe|define)\s*(?:' + _ADMET + '|' + _REVERSE + r')'
    r'(?:\s+in\s+(?:simple|plain|general)\s+(?:terms|language))?[.!?]?\s*', re.I)


def _error():
    from .decision_request import DecisionAdmissionError
    raise DecisionAdmissionError('request_clarification_required')


def rejection(envelope, reason):
    """Fixed supported-count notice; no query/provider text in public errors."""
    from .decision_request import DecisionAdmissionError
    error = DecisionAdmissionError(reason)
    retrieval = _RETRIEVAL.fullmatch(envelope.query)
    prose_count = _PROSE_COUNT.search(envelope.query)
    if (reason == 'request_clarification_required' and (
            retrieval is not None and envelope.rag_count != 3
            or prose_count is not None and _RETRIEVAL.fullmatch(envelope.query[:prose_count.start()]))):
        error.notice = RAG_COUNT_NOTICE
    return error


def _analysis_scope(query):
    """A positional grammar extending A1's shared-input phrase reduction.

    Target+activity and reverse+returned-target are typed obligation phrases,
    not global keywords removed from the request. Unknown text is never skipped.
    The original query is preserved for every parser, requirement and tool.
    """
    from .decision_request import _METRICS, _subjects, _reduce_input_phrases
    from src.agent.tools.molecular_input import _MARKER
    from src.target_identifiers import TARGET_PATTERN

    activity = bool(re.search(r'活性|\b(?:activity|potency|pic50|ic50)\b', query, re.I))
    subjects = _subjects(query, activity=activity)
    structure = re.compile(r'(?<![A-Za-z0-9_])(?:' + '|'.join(
        re.escape(s) for s in sorted(set(subjects), key=len, reverse=True))
        + r')(?![A-Za-z0-9_])') if subjects else None
    activity_noun = r'(?:分子活性|活性|\b(?:activity|potency|pic50|ic50)\b)'
    patterns = [
        ('linked', _LINKED), ('reverse', _REVERSE), ('admet', _ADMET),
        ('activity', r'(?:' + TARGET_PATTERN.pattern + r'\s*(?:的)?\s*)?' + activity_noun),
        ('property', r'分子性质|分子属性|理化性质|性质|属性|\bpropert(?:y|ies)\b'),
        ('likeness', r'类药性|成药性|\b(?:lipinski|drug[ -]?likeness)\b'),
        *((name, pattern) for name, pattern in _METRICS.items()),
    ]
    obligations = [(name, re.compile(pattern, re.I)) for name, pattern in patterns]
    ordinal = r'第(?:[一二三四五六七八九十]{1,3}|[1-9][0-9]?)个'
    syntax = re.compile('|'.join('(?P<' + name + '>' + pattern + ')' for name, pattern in (
        ('action', r'计算|预测|评估|分析|\b(?:calculate|compute|predict|assess|evaluate|analy[sz]e)\b'),
        ('reference', r'刚才(?:\s*' + ordinal + r')?|' + ordinal + r'|上一个|这个|该|\b(?:first|second|third|previous|selected)\b'),
        ('subject', r'候选(?:分子|化合物)?|分子|化合物|\b(?:candidate(?:\s+(?:molecule|compound))?|molecule|compound)\b'),
        ('plural', r'\b(?:molecules|compounds|candidates)\b'),
        ('join', r'然后|和|及|与|并|\b(?:and|then)\b'),
        ('include', r'包含|\bincluding\b'),
        ('relation', r'针对|的|对|\b(?:of|for|with|against)\b'),
        ('article', r'\b(?:molecular|the)\b'),
        ('polite', r'请|帮我|\bplease\b'),
        ('separator', r'[，,;；、\r\n]+'), ('end', r'[。!?！？]'),
        ('space', r'[^\S\r\n]+|[()（）\x22\x27`]'),
    )), re.I)
    position, stream, consumed, found = 0, [], [], set()
    seen_action = False
    while position < len(query):
        match = structure.match(query, position) if structure is not None else None
        role = 'explicit' if match else None
        if match:
            consumed.append(match.group())
        if match is None and subjects:
            match = _MARKER.match(query, position)
            role = 'field' if match else None
        if match is None:
            for name, pattern in obligations:
                match = pattern.match(query, position)
                if match:
                    found.add(name)
                    role = 'obligation'
                    break
        if match is None:
            match = syntax.match(query, position)
            role = match.lastgroup if match else None
        if match is None:
            _error()
        if role == 'action':
            if seen_action or found:
                _error()
            seen_action = True
        elif role == 'polite':
            if stream:
                _error()
        elif role != 'space':
            stream.append((role, match.group()))
        position = match.end()
    if not found or tuple(consumed) != subjects:
        _error()
    _reduce_input_phrases(stream, subjects)
    metrics = tuple(name for name in _METRICS if name in found)
    names = set()
    for key, tool in (('property', 'property_calculator'), ('likeness', 'drug_likeness_assessment'),
                      ('admet', 'admet_predictor'), ('activity', 'activity_predictor'),
                      ('reverse', 'reverse_target_predictor')):
        if key in found:
            names.add(tool)
    if metrics:
        names.add('property_calculator')
    if 'linked' in found:
        names.update(('reverse_target_predictor', 'target_database_search'))
    return frozenset(names), metrics, subjects, 'linked' in found


def _reduce(query):
    from .decision_request import _classify, _subjects, _RISK_SCAN
    # A retrieval relation has a data slot, not an executable sub-request. The
    # complete outer phrase is consumed; no free-text action/count tail allowed.
    retrieval = _RETRIEVAL.fullmatch(query)
    if retrieval:
        topic = retrieval['topic'] or ''
        if any(token.lastgroup in {'action', 'evidence'} for token in _RISK_SCAN.finditer(topic.casefold())):
            _error()
        return 'scientific', frozenset({'rag_search'}), (), (), None, bool(retrieval['hits'])
    count = _PROSE_COUNT.search(query)
    if count is not None and _RETRIEVAL.fullmatch(query[:count.start()]):
        _error()  # Fixed-k retrieval never silently discards a prose count.
    if _B_ANALYSIS.search(query):
        required, metrics, subjects, linked = _analysis_scope(query)
        return 'scientific', required, metrics, subjects, 'reverse' if linked else None, False
    kind, required, metrics = _classify(query)
    target = 'user' if 'target_database_search' in required else None
    subjects = _subjects(query, activity='activity_predictor' in required) if kind == 'scientific' and not target else ()
    if target:
        from src.agent.contracts.target_request import analyze_target_request
        if analyze_target_request(query).needs_clarification:
            _error()
    return kind, required, metrics, subjects, target, False


def prepare(envelope, *, references=None, resolve_references=True, ordinary=False):
    from .decision_request import DecisionAdmissionError, PreparedDecision
    try:
        query = envelope.query
        kind, required, metrics, subjects, target, hits = (
            ('chat', frozenset(), (), (), None, False) if ordinary else _reduce(query))
        if required - {'rag_search'} and not envelope.enable_tools:
            raise DecisionAdmissionError('scientific_tools_disabled')
        if 'rag_search' in required:
            if not envelope.enable_rag:
                raise DecisionAdmissionError('scientific_tools_disabled')
            if envelope.rag_count != 3:
                raise rejection(envelope, 'request_clarification_required')
        selected = None
        molecular = required - {'rag_search', 'target_database_search'}
        if molecular and not subjects and resolve_references:
            if references is not None:
                selected = references.resolve(query, envelope.reference, envelope.selection,
                    session_id=envelope.session_id, enable_tools=envelope.enable_tools)
                if selected is not None:
                    subjects = (selected.canonical_smiles,)
            elif envelope.reference is not None or envelope.selection is not None:
                raise DecisionAdmissionError('scientific_reference_unavailable')
        analyses = []
        if 'admet_predictor' in required:
            analyses.append(AdmetRequirement(tool_name='admet_predictor', expected_smiles=subjects,
                exact_molecule_count=len(subjects) or None))
        if 'activity_predictor' in required:
            from src.agent.tools.base_tool import BaseMolecularTool
            from src.agent.tools.activity_input import activity_target
            from src.target_identifiers import target_mentions
            # The activity parser validates supported families, but may return
            # the first of several identities in one family. A single v2 target
            # cannot represent PDE5A AND PDE4A: reject before choosing either.
            # Exact identifier aliases (e.g. BCHE/BuChE) remain one identity.
            if len(target_mentions(query)) > 1:
                _error()
            authored = activity_target(query, BaseMolecularTool('admission', '').validate_smiles)
            analyses.append(ActivityRequirement(tool_name='activity_predictor', expected_smiles=subjects,
                exact_molecule_count=len(subjects) or None, target=authored))
        if 'reverse_target_predictor' in required and len(subjects) > 1:
            _error()
        requirements = BindingRequirements(version='2', profile=B1_PROFILE_REVISION,
            molecular_results=tuple(MolecularRequirement(tool_name=name, expected_smiles=subjects,
                exact_molecule_count=len(subjects) or None,
                required_metrics=metrics if name == 'property_calculator' else ('lipinski_compliant',))
                for name in sorted(required & {'property_calculator', 'drug_likeness_assessment'})),
            analysis_results=tuple(analyses),
            reverse_result=ReverseRequirement(expected_smiles=subjects[0])
                if subjects and 'reverse_target_predictor' in required else None,
            retrieval_result=RetrievalRequirement(query=query, require_hits=hits) if 'rag_search' in required else None,
            target_result=TargetRequirement(input=target, query=query if target == 'user' else None)
                if target else None)
        context = AgentContext(query, envelope.trace_id, user_id=envelope.session_id,
            session_id=envelope.session_id, temperature=envelope.temperature, mol_count=envelope.mol_count,
            metadata={'capabilities': {'scientific_tools': envelope.enable_tools, 'rag': envelope.enable_rag},
                      'rag_count': envelope.rag_count}, resolved_molecule=selected)
        if contains_secret_material(context_value(context, query_content_bytes=True)):
            raise DecisionAdmissionError('sensitive_input_rejected')
        requirements = prepare_binding_requirements(requirements, context=context, request_kind=kind,
            allowed_tools=required, required_tools=required)
        return PreparedDecision(query, envelope.session_id, envelope.trace_id, envelope.enable_tools,
            envelope.enable_rag, envelope.temperature, envelope.mol_count, envelope.rag_count,
            replace(selected) if selected is not None else None, kind, required, required,
            requirements, envelope.config_generation)
    except DecisionAdmissionError as exc:
        raise rejection(envelope, exc.code) from None
    except (DecisionBoundaryError, ValueError, TypeError, ImportError):
        _error()


def assess(envelope):
    from src.agent.evidence.ledger import EvidenceLedger
    from .decision_request import DecisionAdmissionError, WholeRequestAssessment, _whole_risk
    digest = EvidenceLedger.output_digest(envelope.query)
    def result(kind, reason):
        return WholeRequestAssessment('1', kind, reason, digest)
    if _B_DESCRIPTION.fullmatch(envelope.query):
        return result('semantic_candidate', 'intent_required')
    risk = _whole_risk(envelope.query)
    # Extend only the risk scan's nominal vocabulary, NOT the scientific parser
    # or request. Complete descriptive scope still belongs to the existing
    # whole-risk grammar; an unknown suffix after a new B noun cannot become chat.
    risk_view = unicodedata.normalize('NFKC', envelope.query).casefold()
    b_risk = _whole_risk(_B_ANALYSIS.sub('admet', risk_view))
    if risk == 'clear':
        risk = b_risk
    if risk == 'ambiguous':
        return result('blocked', 'request_clarification_required')
    try:
        prepared = prepare(envelope, resolve_references=False)
    except DecisionAdmissionError as exc:
        if risk == 'clear':
            return result('semantic_candidate', 'intent_required')
        return result('blocked', exc.code if exc.code in (
            'unsupported_scientific_request', 'scientific_tools_disabled') else 'request_clarification_required')
    if prepared.request_kind == 'scientific':
        return result('known_scientific', 'supported_scientific_request')
    if risk != 'clear':
        return result('blocked', 'request_clarification_required')
    return result('known_chat', 'closed_chat_request')


def validate_reply_scope(query, *, original_query, request_kind, required_tools):
    """Pure whole-reply grammar, not admission or scientific input reduction.

    The authenticated B8 journal owns identities and monotonic refinement. This
    guard only prevents a reply from adding execution/count/permission clauses.
    It deliberately does not assemble a new request from clarification fragments.
    """
    from .decision_request import _whole_risk
    from src.target_identifiers import TARGET_PATTERN
    if (type(query) is not str or not query.strip() or len(query) > 16384
            or len(query.encode('utf-8')) > 16384 or contains_secret_material(query)):
        _error()
    if request_kind == 'chat':
        if _whole_risk(query) != 'clear' or _B_ANALYSIS.search(query):
            _error()
        return
    if query == original_query or query.strip().casefold() in {'继续', 'continue'}:
        return
    molecular = required_tools - {'rag_search', 'target_database_search'}
    fields = re.split(r'[；;\r\n]+', query.strip())
    seen = set()
    for field in fields:
        match = re.fullmatch(r'\s*(SMILES|target|靶点)\s*[:：=]\s*(\S+)\s*', field, re.I)
        if match is None:
            _error()
        name, value = match.groups()
        name = 'smiles' if name.casefold() == 'smiles' else 'target'
        if name in seen or not molecular:
            _error()
        seen.add(name)
        if name == 'target' and ('activity_predictor' not in required_tools
                                 or TARGET_PATTERN.fullmatch(value) is None):
            _error()
