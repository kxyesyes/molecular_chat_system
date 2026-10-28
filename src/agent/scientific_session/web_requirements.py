"""Compile domain obligations, never execute a workflow or invoke a model."""
from __future__ import annotations

from dataclasses import fields, replace
import json
import math
import re

from src.agent.contracts.web_requirements import (
    WEB_PROFILE_REVISION, WEB_TOOLS, parse_web_requirements, plain_json, web_plan_digest,
)
from src.agent.contracts.concept_request import is_concept_explanation as _concept_explanation
from src.agent.orchestrators.base import WorkflowStep
from src.agent.planning.compiler import PlanCompiler
from src.agent.planning.task_planner import TaskPlanner, WorkflowPlan
from src.agent.planning.request_parsing import (
    positive_intent_text, property_calculation_forbidden,
    target_lookup_result_annotation,
)
from src.agent.workflows import WorkflowCatalog, WorkflowPolicy
from src.agent.utils.validators import InputValidator
from .decision_bounds import context_value

_ERROR = 'invalid_web_requirements'
_PATTERNS = {
    'property_calculator': r'性质|属性|理化|描述符|分子量|\b(?:propert(?:y|ies)|descriptors?|MW|logP|TPSA|QED|HBA|HBD)\b',
    'drug_likeness_assessment': r'类药性|成药性|lipinski|drug[ -]?likeness',
    'admet_predictor': r'\badmet?\b|吸收|分布|代谢|排泄|毒性',
    'activity_predictor': r'活性|(?<![A-Za-z0-9_])(?:activity|potency|pIC50|IC50|inhibition)(?![A-Za-z0-9_])',
    'reverse_target_predictor': r'反向寻靶|反向预测靶点|\breverse\s+target',
    'rag_search': r'知识库|数据库|文献|\b(?:rag|knowledge\s+base|database|literature)\b',
    'target_database_search': r'(?:查找|检索|搜索|查询|find|search|lookup|look\s+up).*(?:靶点|结构|\btarget|\bPDB|\bprotein)',
    'candidate_ranker': r'筛选|排序|\b(?:rank|ranking|screen)\b',
}


def _ordinary_knowledge_lookup(query):
    # A whole request for explanatory material, not a prefix that can mask
    # another operation. Domain/tool coverage still runs independently.
    return re.fullmatch(r'\s*请?\s*(?:检索|搜索|查找)关于\s*'
        r'[A-Za-z0-9_+#/.\- ]{1,80}\s*的(?:普通|开发)?说明[。.!！]?\s*', query) is not None


def _names(values):
    if type(values) not in (list, tuple, set, frozenset) or len(values) > 32:
        raise ValueError(_ERROR)
    if any(type(v) is not str or v not in WEB_TOOLS for v in values):
        raise ValueError(_ERROR)
    return frozenset(values)


def _tuple_paths(value, path=''):
    paths = [path] if type(value) is tuple else []
    if type(value) in (tuple, list):
        for index, item in enumerate(value):
            paths += _tuple_paths(item, path + '/' + str(index))
    elif type(value) is dict:
        for key, item in value.items():
            paths += _tuple_paths(item, path + '/' + key.replace('~', '~0').replace('/', '~1'))
    return paths


def _step_wire(step):
    raw = {f.name: getattr(step, f.name) for f in fields(WorkflowStep)}
    raw['tuple_paths'] = sorted(_tuple_paths(step.input_data, '/input_data') + _tuple_paths(step.metadata, '/metadata'))
    return plain_json(raw)


def workflow_steps(requirements):
    """Return detached exact WorkflowSteps, restoring every recorded tuple."""
    requirements = parse_web_requirements(requirements)
    steps = []
    for step in requirements.steps:
        raw = plain_json(step)
        paths = set(raw.pop('tuple_paths'))
        def restore(value, path=''):
            if type(value) is dict:
                return {k: restore(v, path + '/' + k.replace('~', '~0').replace('/', '~1')) for k, v in value.items()}
            if type(value) is list:
                items = [restore(v, path + '/' + str(i)) for i, v in enumerate(value)]
                return tuple(items) if path in paths or path == '/preconditions' else items
            return value
        steps.append(WorkflowStep(**restore(raw)))
    return steps


def _forbidden(query):
    # Reuse the routing/planning negation scope, not substring removal from an
    # arbitrary positive query. A prohibited default endpoint is never granted.
    positive = positive_intent_text(query)
    denied = set()
    for clause in re.split(r'[;；。\n,，]', query):
        if positive_intent_text(clause).strip() == clause.strip():
            continue
        tail = re.split(r'不要|无需|不需要|不做|不运行|不预测|禁止|跳过|避免|\bwithout\b|\bdo\s+not\b|\bdon.t\b|\bskip\b|\bexclude\b', clause, maxsplit=1, flags=re.I)
        if len(tail) == 2:
            denied.update(name for name, pattern in _PATTERNS.items() if re.search(pattern, tail[1], re.I))
    if property_calculation_forbidden(query):
        denied.add('property_calculator')
    # Explicit simultaneous positive/negative endpoint demands need clarification.
    for name in denied:
        if re.search(_PATTERNS[name], positive, re.I):
            if name == 'property_calculator' and 'llm_molecular_generator' not in denied:
                from src.agent.contracts.generation_request import has_generation_intent
                if has_generation_intent(positive):
                    continue  # A design constraint does not authorize calculation.
            raise ValueError('request_clarification_required')
    return frozenset(denied)


def _comparison_ranking_covered(query, tools, workflow):
    from src.agent.evidence_followup import comparison_tools
    if workflow != 'comprehensive_evaluation' or tools != set(comparison_tools(query)):
        return False
    # These are the existing report's evidence-ordering semantics, not a
    # candidate-design ranker call. Remove only the supported phrases: any
    # additional ranking/screening demand must still have its own obligation.
    remaining, count = re.subn(
        r'按(?:可用且通过检查的证据|活性证据|预测\s*pIC50\s*降序)排序',
        '', query, flags=re.I)
    remaining, english_count = re.subn(r'^(\s*compare)\s+and\s+rank\b',
        r'\1', remaining, flags=re.I)
    # Only the report's default evidence ordering is supported here. An
    # explicit English criterion/direction must never be silently replaced.
    if re.search(r'\b(?:by|ascending|descending|order)\b', remaining, re.I):
        return False
    return (bool(count or english_count)
        and bool(re.search(_PATTERNS['activity_predictor'], remaining, re.I))
        and not re.search(_PATTERNS['candidate_ranker'] + r'|\bcandidate_ranker\b', remaining, re.I))


def _reverse_display_ranking_covered(query, tools, workflow):
    if workflow != 'reverse_target_prediction' or tools != {'reverse_target_predictor'}:
        return False
    # Showing an existing rank in the fresh reverse result is presentation,
    # not candidate design. Keep every other rank/screen demand uncovered.
    remaining, count = re.subn(r'\bshow\s+rank\s+1\s+target\b', '', query, flags=re.I)
    return bool(count) and not re.search(
        _PATTERNS['candidate_ranker'] + r'|\bcandidate_ranker\b', remaining, re.I)


def _coverage(query, steps, workflow):
    """Check all explicit operation families; no ignored trailing demands.

    This does not claim general natural-language understanding. Unknown action
    clauses fail closed, while constraints remain in the immutable original
    query for the existing domain validators/optimization verifier.
    """
    from src.agent.contracts.generation_request import has_generation_intent
    from src.agent.evidence_followup import comparison_tools
    from src.agent.planning.comparison_request import comparison_request
    if comparison_tools(query) and not comparison_request(query).supported:
        raise ValueError('uncovered_comparison_obligation')
    positive = positive_intent_text(query)
    tools = {s.tool_name for s in steps}
    if re.search(r'发送|邮件|写入|删除|下载|上传|运行命令|执行命令|\b(?:email|send|delete|download|upload|shell|execute\s+code)\b', positive, re.I):
        raise ValueError('unsupported_scientific_request')
    from src.agent.contracts.docking_intent import explicit_docking_action
    if explicit_docking_action(positive):
        raise ValueError('docking_consent_required')
    target_lookup_annotation = target_lookup_result_annotation(query)
    generation_requested = (
        (has_generation_intent(query) and not target_lookup_annotation)
        or any(
            has_generation_intent(clause)
            and not target_lookup_result_annotation(clause)
            for clause in re.split(r'[;；。\n]', positive)
        )
    )
    if generation_requested and 'llm_molecular_generator' not in tools:
        raise ValueError('uncovered_web_obligation')
    for name, pattern in _PATTERNS.items():
        if not re.search(pattern, positive, re.I) or name in tools:
            continue
        if name == 'candidate_ranker' and (
                _comparison_ranking_covered(positive, tools, workflow)
                or _reverse_display_ranking_covered(positive, tools, workflow)):
            continue
        # Generation adjectives/bounds are not independent calculation calls.
        if 'llm_molecular_generator' in tools and name in {'drug_likeness_assessment', 'property_calculator'}:
            if not re.search(r'(?:计算|评估|calculate|assess)\s*[^;；。]{0,30}(?:' + pattern + ')', positive, re.I):
                continue
        raise ValueError('uncovered_web_obligation')
    # Detect later independent imperatives outside the bounded domain catalog.
    action = (r'(?:计算|预测|评估|执行|运行|查询|查找|检索|搜索|处理|制作|生成|设计|优化|筛选|排序|'
              r'calculate\b|predict\b|assess\b|execute\b|run\b|find\b|search\b|make\b|process\b|'
              r'generate\b|design\b|rank\b|screen\b)')
    boundary = (r'[;；。\n]|\band\s+then\b|(?:[，,]|并且|并|然后|以及|\band\b)\s*'
                r'(?=(?:请\s*|please\s+)?' + action + ')')
    for clause in re.split(boundary, positive, flags=re.I):
        clause = clause.strip()
        if not clause:
            continue
        if re.search(r'生成|设计|优化|全面|综合|降低|提高|改善|\b(?:generat\w*|design|optimi[sz]\w*|comprehensive|lower|higher)\b', clause, re.I):
            continue
        if any(re.search(pattern, clause, re.I) for pattern in _PATTERNS.values()):
            continue
        if tools == {'rag_search'} and _ordinary_knowledge_lookup(positive):
            continue
        if re.match(r'(?:请)?(?:再|并|然后)?(?:计算|预测|评估|执行|运行|查询|查找|检索|搜索|处理|制作)|(?:please\s+)?(?:calculate|predict|assess|execute|run|find|search|make|process)\b', clause, re.I):
            raise ValueError('uncovered_web_obligation')


def compile_web_requirements(context, *, allowed_tools):
    """Build a sealed plan using only existing deterministic planning functions."""
    original = context_value(context)
    allowed = _names(allowed_tools)
    if not context.query.strip() or not 0 <= context.temperature <= 2 or not math.isfinite(context.temperature):
        raise ValueError(_ERROR)
    caps = context.metadata.get('capabilities', {})
    if type(caps) is not dict or any(type(caps.get(k, True)) is not bool for k in ('scientific_tools', 'rag')):
        raise ValueError(_ERROR)
    if not caps.get('scientific_tools', True):
        allowed &= {'rag_search'}
    if not caps.get('rag', True):
        allowed -= {'rag_search'}
    catalog = WorkflowCatalog()
    policy = catalog.get(context.active_skill) if context.active_skill else None
    explanation = not context.active_skill and _concept_explanation(context.query)
    if context.active_skill and policy is None:
        raise ValueError('unsupported_scientific_request')
    from src.agent.planning.composite_request import (
        parse_target_structure_molecule_request,
    )
    if policy is None and parse_target_structure_molecule_request(context.query) is not None:
        policy = catalog.require('target_structure_molecule_analysis')
    if policy is None and not explanation:
        from src.agent.router import SkillRouter
        # A confirmed server identity supplies the molecular subject for
        # routing just as it does for TaskPlanner. Keep the original request
        # unchanged in the sealed requirements and never read model history.
        routing_query = (context.resolved_molecule.routing_query(context.query)
                         if context.resolved_molecule is not None else context.query)
        from src.agent.routing.reverse_request import reverse_rank_routing_query
        routing_query = reverse_rank_routing_query(routing_query)
        decision = SkillRouter(catalog).decide(routing_query, llm=None)
        policy = catalog.get(decision.selected_skill) if decision.selected_skill else None
        if decision.requires_confirmation:
            if 'target_clarification_required' in decision.reasons:
                raise ValueError('target_clarification_required')
        if (policy is None and decision.requires_confirmation
                and decision.reasons != ['Routing confidence is below the execution threshold']):
            raise ValueError('request_clarification_required')
        if policy is None and _ordinary_knowledge_lookup(context.query):
            policy = catalog.require('rag_search')
        if policy is None and caps.get('rag', True):
            molecular_input = InputValidator().analyze_molecular_input(context.query)
            # The Web's bare-structure retrieval is an explicit obligation,
            # not an optional legacy fallback. Match the entire current query
            # so a molecular prefix cannot hide another requested operation.
            if (not molecular_input.blocks_execution
                    and molecular_input.valid_smiles == (context.query.strip(),)):
                policy = catalog.require('rag_search')
    forbidden = _forbidden(context.query)
    if policy is None:
        if not explanation:
            _coverage(context.query, [], 'ordinary_chat')
        plan = WorkflowPlan('ordinary_chat', [], {})
        dependencies = {}
    else:
        if policy.name == 'docking_simulation':
            raise ValueError('docking_consent_required')
        from src.agent.contracts.generation_request import preflight_generation_request
        preflight_generation_request(context.query, context.metadata.get('requested_count'),
            count_supplied='requested_count' in context.metadata,
            active_molecular_skill='llm_molecular_generator' in policy.allowed_tools)
        plan = TaskPlanner().plan(replace(context, active_skill=policy.name))
        if not plan.steps:
            raise ValueError(plan.metadata.get('reason', 'request_clarification_required'))
        from src.agent.contracts.property_ranking_request import property_only_design_request
        if policy.name == 'target_driven_design' and property_only_design_request(context.query):
            # Only the explicit property basis is authorized for this bounded
            # request. Do not pass optional prediction evidence into ranking.
            plan.steps = [s for s in plan.steps if s.tool_name not in ('admet_predictor', 'activity_predictor')]
            plan.metadata['docking_top_n'] = plan.metadata['requested_count']
            plan.steps = [replace(s, metadata={**s.metadata,
                'docking_top_n': plan.metadata['requested_count'],
                'workflow_output_keys': ('molecules', 'properties'),
                'workflow_optional_output_keys': (),
                'required_evidence': ['properties'],
                'ranking_scope': 'properties_only',
                'workflow_metadata_keys': ('docking_top_n', 'target', 'endpoint', 'units', 'required_evidence', 'ranking_scope')
                }) if s.tool_name == 'candidate_ranker' else s
                for s in plan.steps]
        if (plan.workflow_name == 'molecular_design'
                and not any(s.tool_name == 'property_calculator' for s in plan.steps)
                and 'property_calculator' not in forbidden
                and re.search(r'(?:计算|评估|calculate|assess)\s*[^;；。]{0,30}(?:' + _PATTERNS['property_calculator'] + ')',
                              positive_intent_text(context.query), re.I)):
            # The legacy planner adds this exact bound step for numeric bounds.
            # Explicit calculation is the same obligation, not an opaque tool.
            from src.agent.planning.step_templates import molecular_design_steps
            plan.steps = molecular_design_steps(plan.steps[0].input_data, include_properties=True)
            plan.metadata['atomic'] = False
        plan.steps = [s for s in plan.steps if s.tool_name not in forbidden]
        if context.metadata.get('allow_molecular_clarification') is True:
            # The Web compiler seals its exact obligations; the broader legacy
            # ADMET template must not add a new assessment during clarification.
            pending = _names(context.metadata.get('molecular_clarification_tools', ()))
            if not pending or not pending <= {s.tool_name for s in plan.steps}:
                raise ValueError('request_clarification_required')
            plan.steps = [s for s in plan.steps if s.tool_name in pending]
        if not plan.steps:
            raise ValueError('request_clarification_required')
        for step in plan.steps:
            # Raw Web admission has no preselected workflow. Explicit domain
            # contexts may still model a missing-input clarification, whose
            # resolver prevents dispatch until that input has been supplied.
            # Generated candidates are supplied by their declared dependency;
            # selected references are already bound by TaskPlanner. A raw
            # property request must instead contain its own valid structure,
            # never a chemical name or a structure borrowed from chat history.
            if (not context.active_skill and step.tool_name == 'property_calculator'
                    and not (step.input_binding or step.input_from)):
                if type(step.input_data) is not str:
                    raise ValueError(_ERROR)
                molecular_input = InputValidator().analyze_molecular_input(step.input_data)
                if (molecular_input.blocks_execution or (not molecular_input.valid_smiles
                        and context.metadata.get('allow_molecular_clarification') is not True)):
                    raise ValueError('molecular_input_required')
        _coverage(context.query, plan.steps, plan.workflow_name)
        planned_tools = {s.tool_name for s in plan.steps}
        if 'rag_search' in planned_tools and not caps.get('rag', True):
            raise ValueError('rag_disabled')
        if not planned_tools <= allowed:
            raise ValueError('task_requirements_not_authorized')
        compiled = PlanCompiler().compile(plan, policy)
        plan, dependencies = compiled.plan, compiled.dependencies
    raw = dict(version='1', profile=WEB_PROFILE_REVISION, workflow_name=plan.workflow_name,
        request=original, steps=[_step_wire(s) for s in plan.steps],
        dependencies=plain_json(dependencies), metadata=plain_json(plan.metadata),
        forbidden_tools=sorted(forbidden))
    raw['plan_sha256'] = web_plan_digest(raw)
    return parse_web_requirements(raw)


def required_web_tools(requirements, required_tools=()):
    """Every planned action remains an obligation, including optional attempts."""
    requirements = parse_web_requirements(requirements)
    return frozenset(s.tool_name for s in requirements.steps) | _names(required_tools)


def prepare_web_requirements(value, *, context, request_kind, allowed_tools, required_tools):
    requirements = parse_web_requirements(value)
    if request_kind not in ('chat', 'scientific'):
        raise ValueError(_ERROR)
    if (request_kind == 'scientific') != bool(requirements.steps):
        raise ValueError('inconsistent_web_requirements')
    required = required_web_tools(requirements, required_tools)
    if not required <= _names(allowed_tools) or required & set(requirements.forbidden_tools):
        raise ValueError('task_requirements_not_authorized')
    rebuilt = compile_web_requirements(context, allowed_tools=allowed_tools)
    if rebuilt != requirements:
        raise ValueError('web_requirements_context_mismatch')
    return requirements
