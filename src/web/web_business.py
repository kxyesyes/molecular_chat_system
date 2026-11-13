"""Socket-local scientific projections; no model or workflow execution.

The runtime creates one object per accepted turn. Its detached entry is published
only after terminal delivery, separately from ordinary model conversation history.
"""
from copy import deepcopy
import html
import json
import logging
import math
import re

from src.agent import evidence_followup
from src.agent.contracts import RunOutcome
from . import property_comparison
from .property_constraints import generation_constraint_report
from .ranked_target_structures import rank_followup_kind, ranked_structure_followup
from .scientific_obligations import OPTIMIZATION_INTERPRETATION


logger = logging.getLogger(__name__)
_AFFINITY_COMPARISON = re.compile(
    r'\s*这是否意味着\s*(?P<first>[A-Za-z0-9_-]+)\s*的结合亲和力是\s*'
    r'(?P<second>[A-Za-z0-9_-]+)\s*的\s*\d+(?:\.\d+)?\s*倍[？?。]?\s*', re.I)
_AFFINITY_INFERENCE = re.compile(
    r'\s*这意味着它对\s*(?P<first>[A-Za-z0-9_-]+)\s*的结合亲和力一定是\s*'
    r'(?P<second>[A-Za-z0-9_-]+)\s*的\s*\d+(?:\.\d+)?\s*倍吗[？?]?\s*', re.I)
_PRIOR_RESULT_REFERENCE = re.compile(
    r'刚才|之前|先前|上一轮|上次|上述|前述|上面(?:的)?(?=(?:这|两|候选|分子|结果|计算|预测))|这(?:个|些|两|说明|意味|能)|'
    r'那(?:个|些|两)|两者|二者|它|第\s*[一二三四五六七八九十0-9]+\s*个|'
    r'\b(?:previous|prior|earlier|above|these|those|this|that|they|them|it|both|first|second)\b', re.I)
UNVERIFIED_NAME_WARNING = '名称与所给结构的对应关系未核验；结果基于提供的 SMILES。'
_IC50_KI_CONDITIONS = (
    '在可逆、竞争性抑制且满足适用的平衡或稳态条件时，'
    'Cheng–Prusoff 关系为 Ki = IC50 / (1 + [S]/Km)。'
    '需要同一测定条件下的底物浓度 [S] 和 Km，并确认竞争性机制；'
    '紧密结合、底物耗竭或时间依赖性等情况可能使简单换算不适用。'
    '缺少这些条件时不能确定 Ki；这些数值来自用户给定的假设。')


def _estimate_ki(ic50_text, ic50_unit, inputs):
    from decimal import Decimal, localcontext
    scales = {'pm': Decimal('1e-12'), 'nm': Decimal('1e-9'),
              'μm': Decimal('1e-6'), 'um': Decimal('1e-6'), 'mm': Decimal('1e-3')}
    ic50 = Decimal(ic50_text)
    substrate = Decimal(inputs['substrate']) * scales[inputs['substrate_unit'].casefold()]
    km = Decimal(inputs['km']) * scales[inputs['km_unit'].casefold()]
    if ic50 <= 0 or substrate < 0 or km <= 0:
        return {'content': 'IC50 和 Km 必须为正，底物浓度不能为负；请核对数据。', 'status': 'partial'}
    with localcontext() as ctx:
        ctx.prec = 6
        ratio = substrate / km
        ki = ic50 / (1 + ratio)
    return {'content': (f"按你提供的假设：IC50 = {ic50_text} {ic50_unit}，"
            f"底物浓度 = {inputs['substrate']} {inputs['substrate_unit']}，"
            f"Km = {inputs['km']} {inputs['km_unit']}。"
            f"[S]/Km = {ratio:.3g}；Ki = IC50 / (1 + [S]/Km) ≈ {ki:.3g} {ic50_unit}。"
            '数值最多显示三位有效数字，实验不确定度未提供。'
            '这是基于用户假设的估算，不是独立实验测定；'
            '竞争性机制及适用的平衡或稳态条件需核实，紧密结合、底物耗竭或时间依赖性可能使该关系不适用。'),
            'status': 'completed'}
_DOCKING_PREPARATION_ADVICE = ('已提供受体 PDB 编号和配体 SMILES，但本次尚未核验结构或口袋。'
    '对接前需确认受体链、构象、缺失残基及配体和辅因子；准备受体的质子化状态、氢和必要的水。'
    '还需生成并检查配体三维构象和质子化状态，并依据经核实的口袋或参考配体确定对接盒子的中心与大小（Å）。'
    '随后应检查工具可用性和参数，并考虑用已知共晶配体重对接验证流程。'
    '本次未检索或准备结构，也未执行对接；对接评分不能直接等同于实验亲和力。')


def research_abstract_message(source, question):
    identifiers = re.findall(r'(?<![A-Za-z0-9])[A-Za-z]{1,4}[0-9]{1,5}[A-Za-z]{1,3}(?![A-Za-z0-9])', question)
    missing = list(dict.fromkeys(identifier for identifier in identifiers
        if not re.search(r'(?<![A-Za-z0-9])' + re.escape(identifier) + r'(?![A-Za-z0-9])',
                         source['abstract'], re.I)))
    note = (f"摘要中未出现 {'、'.join(missing)}；不能据此判断全文是否有相关实验。" if missing else '')
    return (f"已找到 [PubMed {source['pmid']}]({source['url']}) 的摘要。" + note
            + '尚未形成经核对的研究结论，请查阅完整论文。')


def _has_unverified_molecule_name(query, tool_results):
    observed = [row['smiles'] for item in tool_results
        if item.tool_name in {'property_calculator', 'admet_predictor'} and isinstance(item.data, list)
        for row in item.data if isinstance(row, dict) and isinstance(row.get('smiles'), str) and row['smiles']]
    spans = [match.span() for smiles in observed for match in re.finditer(re.escape(smiles), query)]
    names = re.finditer(
        r'(?:计算|分析|预测|评估|比较|对比|和|与|、)\s*([\u4e00-\u9fffA-Za-z0-9_-]{2,32})\s*[（(][^）)]+[）)]', query)
    labels = []
    for smiles in observed:
        if smiles not in query:
            continue
        segments = re.split(r'和|与|、|，|,', query.split(smiles, 1)[0])
        label = property_comparison._adjacent_molecule_label(query, smiles)
        if not label and len(segments) > 1:
            label = property_comparison._adjacent_molecule_label('计算 ' + segments[-1] + smiles, smiles)
        labels.append(label)
    return (any(label and label not in {'分子', '化合物', '结构'}
                and not re.fullmatch(r'[A-Z]\s*的\s*SMILES\s*(?:是|为|[:：=])', label)
                for label in labels)
            or any(match.group(1) not in {'分子', '化合物', '结构'}
                   and not any(start <= match.start(1) and match.end(1) <= end for start, end in spans)
                   for match in names))


_MODEL_HISTORY_CLAIM = re.compile(
    r'\b(?:previous|prior|earlier)\b|\blast\s+(?:run|result|prediction|value|measurement|experiment|calculation|time)\b|上次|上一轮|之前|刚才|'
    r'\b(?:we\s+(?:obtained|measured|calculated|predicted)|you\s+(?:asked|measured|obtained))\b.{0,100}\b(?:was|is|were)\s+[+-]?\d|'
    r'(?:\byour\b|你的|您的).{0,100}(?:pIC50|IC50|活性|数值|预测).{0,100}'
    r'(?:\b(?:was|is|were)\b|是|为|=|[:：])\s*(?:(?:approximately|about|around|roughly|近似|约)\s*)?[+-]?\d', re.I | re.S)
_PROPERTY_FOLLOWUP = (r'(?:请问)?(?:刚才|上一轮|之前)(?:的)?'
                      r'(?:(?:这|那)?两个分子中哪(?:一)?个(?:的)?\s*|哪(?:一)?个分子的)'
                      r'(?P<metric>log\s*p|分子量|亲脂性)\s*(?:更|较)(?P<direction>高|低|大|小)')
_PROPERTY_FOLLOWUP_END = r'[？?。]?(?:\s*(?:依据是什么|各是多少)[？?。]?)?'
_CANDIDATE_LIPOPHILICITY = (r'(?:请问)?(?:刚才|上一轮|之前)?(?:的)?(?:这|那)?两个(?:候选分子|候选|分子)(?:中)?'
                            r'哪(?:一)?个更亲脂' + _PROPERTY_FOLLOWUP_END +
                            r'(?:\s*(?:请)?(?:根据|基于)(?:刚才|上一轮)(?:的)?\s*log\s*p\s*(?:解释|说明)[。.!！?？]?)?')
_LIVER_FOLLOWUP = (r'(?:这|该结果)(?:是否)?(?:意味着|说明|证明)(?P<subject>[^，,。？?]{1,32}?)'
                   r'(?:一定|必然)(?:会)?(?:导致|引起|造成)(?:肝损伤|肝毒性)')
_NEGATIVE_LIVER_FOLLOWUP = (r'(?:这|该结果)(?:是否)?(?:意味着|说明|证明)(?:这个|该)分子'
                            r'(?:一定|必然|肯定)?(?:没有|不存在)(?:肝毒性|肝损伤)吗')
_BIOAVAILABILITY_UNAVAILABLE = ('无法仅凭理化性质或 Lipinski 规则比较具体药物的口服生物利用度；'
    '本次没有可核验的药代比较数据。需要明确比较原形药物还是代谢物，'
    '并核对相应制剂、剂量和口服与静脉给药的药代研究。')


def _bioavailability_comparison(query):
    subject = r'[\u4e00-\u9fffA-Za-z0-9-]{2,30}?'
    return re.fullmatch(
        r'\s*(?:所以|那么)?\s*(?:'
        + subject + r'的(?:口服)?生物利用度(?:一定|肯定|通常)?比' + subject
        + r'(?:的(?:口服)?生物利用度)?(?:更|较)?(?:好|高|低|差)(?:吗|么)?|'
        + subject + r'(?:和|与|及)' + subject + r'[，,]?\s*'
        r'(?:哪个|哪一个|哪种)(?:的)?(?:口服)?生物利用度(?:更|较)?(?:高|低|好|差)(?:吗|么)?)'
        r'[？?。.!！]?\s*(?:(?:请)?(?:给出|提供|列出)(?:大概|具体)?(?:数值|数据)[。.!！?？]?)?\s*',
        query, re.I) is not None


def named_admet_request(query):
    return re.fullmatch(
        r'\s*(?:请\s*)?预测\s*(?P<name>[\u4e00-\u9fffA-Za-z0-9_-]{2,32})的\s*ADMET'
        r'(?:[，,]\s*重点(?:关注|查看|给出)\s*(?:hERG(?:\s*风险)?|肝毒性|水?溶解度)'
        r'(?:\s*(?:和|与|及|、|[,，])\s*(?:hERG(?:\s*风险)?|肝毒性|水?溶解度))*)?'
        r'\s*[。.!！?？]?\s*', query, re.I)


def activity_explanation_text(answer, query):
    """Use the same public labels for standalone and mixed activity discussion."""
    if ('classification_regression_consistent' not in query.casefold()
            and not re.search(r'\b(?:true|false)\b', query, re.I)):
        answer = re.sub(
            r'(?P<label>classification_regression_consistent|分类与回归(?:结果)?(?:一致性|一致)|'
            r'(?<!\w)一致性)\s*(?:=|:|：|为|是)\s*(?P<value>true|false)\b',
            lambda match: ('一致性' if match['label'] == '一致性' else '分类与回归结果')
            + '：' + ('一致' if match['value'].casefold() == 'true' else '不一致'), answer, flags=re.I)
    if not re.search(r'\b(?:species|mixed)\b', query, re.I):
        answer = re.sub(r'(?:species|物种(?:范围)?)\s*(?:=|:|：|为|是)\s*mixed\b',
                        '物种：混合物种', answer, flags=re.I)
        answer = re.sub(r'混合物种\s*[（(]\s*mixed\s*[）)]', '混合物种', answer, flags=re.I)
    labels = {'predicted_pIC50': '预测 pIC50', 'activity_probability': '活性分类分数',
              'activity_class': '预测分类', 'model_scope': '模型范围',
              'family_id': '模型家族', 'target_scope': '靶点范围',
              'species': '物种', 'label_threshold': 'pIC50 分类阈值',
              'probability_threshold': '分类分数阈值',
              'classification_regression_consistent': '分类与回归结果一致',
              'experimental_replication_verified': '实验复现已核验',
              'pde-family-mixed-subtypes': 'PDE 家族（混合亚型）',
              'pde-family': 'PDE 家族'}
    labels = {key: value for key, value in labels.items() if key.casefold() not in query.casefold()}
    return re.sub(r'(?<![A-Za-z0-9_-])(?:' + '|'.join(map(re.escape, labels))
                  + r')(?![A-Za-z0-9_-])', lambda match: labels[match[0]], answer) if labels else answer


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
        self.concept_calculation = None
        mixed = re.fullmatch(rf'(?P<prior>{_PROPERTY_FOLLOWUP})[？?。；;，,]\s*'
                             r'(?:再|然后|并且?)\s*(?P<new>.+)', query.strip(), re.I | re.S)
        if mixed is None:
            for pattern in (_LIVER_FOLLOWUP, _NEGATIVE_LIVER_FOLLOWUP):
                mixed = re.fullmatch(rf'(?P<prior>{pattern})[？?。；;，,]\s*'
                                     r'(?:再|然后|并且?)\s*(?P<new>.+)', query.strip(), re.I | re.S)
                if mixed:
                    break
        bioavailability_mixed = False
        docking_preparation_mixed = False
        self.mixed_activity_discussion = False
        activity_inspection_view = None
        if mixed is None:
            from src.agent.contracts.concept_request import is_concept_explanation
            from src.agent.activity_evidence_inspection import inspection_request
            candidate = re.fullmatch(
                r'(?P<prior>.+?[。.!！?？])\s*(?:再|然后)\s*'
                r'(?P<new>(?:请)?(?:预测|计算)\s+.+)', query.strip(), re.I | re.S)
            inspection = inspection_request(candidate['prior']) if candidate else None
            if (candidate and _PRIOR_RESULT_REFERENCE.search(candidate['prior'])
                    and re.search(r'pIC50|活性|分类分数', candidate['prior'], re.I)
                    and (is_concept_explanation(candidate['prior'])
                         or isinstance(inspection, dict))):
                mixed = candidate
                self.mixed_activity_discussion = True
                if isinstance(inspection, dict):
                    inspected = evidence_followup.followup_answer(candidate['prior'], self.history, self.session_id)
                    activity_inspection_view = {'content': inspected.get('summary_answer', inspected['final_answer']),
                                                'status': inspected['status']}
        corrected_mixed_query = None
        if mixed is None:
            from src.agent.contracts.docking_intent import docking_preparation_consultation
            candidate = re.fullmatch(
                r'(?P<prior>.+?[。.!！?？])\s*(?:再|然后)\s*'
                r'(?P<new>(?:请)?计算\s+(?:SMILES\s*[:：]\s*)?'
                r'[A-Za-z0-9@+\-\[\]()=#\\/%.:*]{1,512}\s*的\s*'
                r'(?:log\s*p|TPSA|分子量|MW|QED|HBA|HBD)'
                r'(?:\s*(?:和|与|及|、|[,，])\s*(?:log\s*p|TPSA|分子量|MW|QED|HBA|HBD))*'
                r'[。.!！?？]?)\s*', query.strip(), re.I | re.S)
            if candidate and docking_preparation_consultation(candidate.group('prior')):
                mixed = candidate
                docking_preparation_mixed = True
        if mixed is None:
            candidate = re.fullmatch(r'(?P<prior>.+?[？?])\s*(?:再|然后)\s*(?P<new>计算\s+.+)',
                                     query.strip(), re.I | re.S)
            if candidate and _bioavailability_comparison(candidate.group('prior')):
                mixed = candidate
                bioavailability_mixed = True
        if mixed is None and self.history and self.history[-1].get('agent_used') is False:
            previous = re.fullmatch(r'(?P<prior>.+?[？?])\s*(?:再|然后)\s*(?P<new>计算\s+.+)',
                                    self.history[-1].get('user', '').strip(), re.I | re.S)
            if previous and _bioavailability_comparison(previous.group('prior')):
                corrected = property_comparison.corrected_property_query(
                    query, [{'user': previous.group('new'), 'agent_used': False}])
                if corrected != query:
                    mixed = previous
                    corrected_mixed_query = corrected
                    bioavailability_mixed = True
        self.mixed_history_query = mixed.group('prior') if mixed else None
        self.mixed_execution_query = (corrected_mixed_query or mixed.group('new')) if mixed else None
        self.mixed_history_view = (
            {'content': _DOCKING_PREPARATION_ADVICE, 'status': 'completed'} if docking_preparation_mixed
            else {'content': _BIOAVAILABILITY_UNAVAILABLE, 'status': 'not_calculated'} if bioavailability_mixed
            else activity_inspection_view)
        self.property_explanation = None
        self.property_calculation_query = None
        self.property_bound_question = False
        pka_partial = re.fullmatch(
            r'(.+?)[，,]\s*并预测(?:它|其|该分子)的\s*pKa\s*(?:和|及|与)\s*'
            r'pH\s*\d+(?:\.\d+)?\s*下的电离比例[。.!！]\s*'
            r'(?:若|如果)无法预测[，,]\s*请明确说明[。.!！?？]?\s*', query.strip(), re.I)
        self.pka_partial_requested = pka_partial is not None
        if pka_partial:
            self.property_calculation_query = pka_partial.group(1)
        ionization_partial = re.fullmatch(
            r'(.+?)[；;]\s*(?:同时|并且?|另外)?\s*(?:请)?(?:告诉我|计算|预测|给出)\s*'
            r'pH\s*\d+(?:\.\d+)?\s*(?:下|时)的?(?:电离|离子化)(?:比例|分数|百分比|率)'
            r'[，,]\s*(?:若|如果)?无法预测(?:的项目)?[，,]?\s*请明确说明[。.!！?？]?\s*',
            query.strip(), re.I)
        self.ionization_partial_requested = ionization_partial is not None
        if ionization_partial:
            self.property_calculation_query = ionization_partial.group(1)
        bound_pattern = (
            r'(.+?)[，,]\s*(?:并|并且)?\s*判断\s*(?:其|该分子的|这个分子的)?\s*'
            r'(?:分子量|MW|logP|TPSA|QED|HBA|HBD)\s*(?:是否)?\s*'
            r'(?:不超过|不大于|不低于|不小于|小于|低于|大于|高于|<=|>=|≤|≥|<|>)\s*'
            r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?\s*'
            r'(?:Da|g/mol|g·mol-1|Å²|A²|A2)?\s*[。.!！?？]?\s*')
        bound_question = re.fullmatch(bound_pattern, query, re.I)
        if bound_question:
            from src.agent.property_constraints import requested_property_bounds
            bounds, unknown = requested_property_bounds(query)
            if len(bounds) == 1 and not unknown:
                self.property_calculation_query = bound_question.group(1)
                self.property_bound_question = True
        comparison_request = re.fullmatch(
            r'(.+?)[，,]\s*(?:并|并且|以及)\s*(?:比较|对比)(?:这些)?结果[。.!！?？]?\s*',
            query.strip())
        self.lipophilicity_comparison = False
        if comparison_request is None:
            lipophilicity = re.fullmatch(
                r'(.+?)[，,]\s*(?:并|并且|以及)?\s*(?:指出|说明|判断)?\s*'
                r'(?:哪一个|哪个)更(?:亲脂|疏水)[。.!！?？]?\s*', query.strip())
            if lipophilicity and re.search(r'(?<![A-Za-z0-9_])c?log\s*p(?![A-Za-z0-9_])',
                                           lipophilicity.group(1), re.I):
                comparison_request = lipophilicity
                self.lipophilicity_comparison = True
        self.property_comparison_requested = comparison_request is not None
        if comparison_request:
            self.property_calculation_query = comparison_request.group(1)
        origin = re.fullmatch(
            r'(.+?)[，,]\s*(?:并|以及)?\s*(?:指出|说明|标明)'
            r'(?:哪些指标是计算值|(?:这些|上述)(?:数值|结果|指标)是否(?:来自)?实验(?:测量|测定))'
            r'[。.!！?？]?\s*',
            query.strip())
        self.property_origin_requested = origin is not None
        if origin:
            self.property_calculation_query = origin.group(1)
        significance = re.fullmatch(
            r'(.+?)[，,]\s*(?:并|以及)?\s*(?:说明|解释)(?:这个|该|这些)?'
            r'(?:数值|结果|指标|描述符)对药物设计的意义[。.!！?？]?\s*', query.strip())
        if significance:
            self.property_explanation = 'descriptor_significance'
            self.property_calculation_query = significance.group(1)
        efficacy = re.fullmatch(
            r'(.+?)[，,]\s*(?:并|以及)?\s*(?:说明|解释)(?:这些|上述)(?:计算)?(?:性质|指标|描述符)'
            r'能否直接证明(?:它们|哪个分子|哪一种)(?:具有)?(?:更好|更强)的?药效[。.!！?？]?\s*',
            query.strip())
        if efficacy:
            self.property_explanation = 'efficacy_limit'
            self.property_calculation_query = efficacy.group(1)
        oral = re.fullmatch(
            r'(.+?)[，,]\s*(?:并|以及)?\s*(?:(?:说明|解释)(?:这些|上述|该)?(?:结果|指标|描述符)对口服成药性的意义|'
            r'根据(?:这些|上述|该)(?:结果|指标|描述符)分析口服成药性(?:差异)?)'
            r'(?:[；;]\s*不要编造实验数据)?[。.!！?？]?\s*',
            query.strip(), re.I)
        if oral:
            self.property_explanation = 'oral_significance'
            self.property_calculation_query = oral.group(1)
        else:
            oral_comparison = re.fullmatch(
                r'(.+?)[，,]\s*(?:并|以及)?\s*(?:说明|解释)(?:这些|上述|两者的)?差异'
                r'对口服吸收的意义[。.!！?？]?\s*', query.strip(), re.I)
            if oral_comparison:
                self.property_explanation = 'oral_comparison'
                self.property_calculation_query = oral_comparison.group(1)
                bound = re.fullmatch(bound_pattern, self.property_calculation_query, re.I)
                if bound:
                    from src.agent.property_constraints import requested_property_bounds
                    bounds, unknown = requested_property_bounds(self.property_calculation_query)
                    if len(bounds) == 1 and not unknown:
                        self.property_calculation_query = bound.group(1)
                        self.property_bound_question = True
            oral_choice = re.fullmatch(
                r'(.+?)[，,]\s*(?:并|以及)?\s*(?:说明|解释)(?:哪一个|哪个)更适合'
                r'(?:口服筛选|(?:作为)?口服(?:药物)?先导物)'
                r'(?:[，,；;]\s*(?:并)?(?:说明|解释)(?:判断的)?局限(?:性)?)?[。.!！?？]?\s*',
                query.strip(), re.I)
            if oral_choice:
                self.property_explanation = 'oral_screening_choice'
                self.property_calculation_query = oral_choice.group(1)
        from src.agent.contracts.concept_request import is_concept_explanation
        parts = re.split(r'(?:[，,]\s*(?:并且?|然后)|[？?。]\s*(?:再|然后)?)\s*(?=计算)', query, maxsplit=1)
        if not self.mixed_activity_discussion and len(parts) == 2 and is_concept_explanation(parts[0]):
            self.concept_calculation = (parts[0], parts[1])
        self.safe_concept_history = None
        self.contextual_property = False
        self.resumed_named_admet = None
        self.solubility_requested_smiles = None
        self.invalid_named_admet_request = None
        supplied = re.fullmatch(r'\s*SMILES\s*[:：]\s*(?P<smiles>[^\s，,；;]+)\s*', query, re.I)
        if supplied and self.history and self.history[-1].get('agent_used') is False:
            previous = self.history[-1].get('pending_named_admet') or self.history[-1].get('user', '')
            subject = named_admet_request(previous)
            if subject:
                from src.agent.tools.base_tool import BaseMolecularTool
                from src.agent.tools.molecular_input import parse_molecular_smiles
                from src.system.molecular_input_parser import MolecularInputInvalid
                try:
                    structures = parse_molecular_smiles('SMILES: ' + supplied['smiles'],
                                                       BaseMolecularTool('named-admet-resume', ''))
                except MolecularInputInvalid:
                    self.invalid_named_admet_request = previous
                    structures = []
                except ValueError:
                    structures = []
                if structures == [supplied['smiles']]:
                    start, end = subject.span('name')
                    self.resumed_named_admet = (previous[:start] + previous[start:end]
                                                + '（SMILES: ' + supplied['smiles'] + '）' + previous[end:])
        self.entry = {'user': query, 'agent_used': False}

    def execution_query(self):
        if re.fullmatch(r'\s*(?:请)?预测(?:这两个|这两种|上述两个)分子的水?溶解度'
                        r'[，,]\s*(?:并)?(?:比较|对比)(?:其|它们的)?预测值[。.!！]?\s*', self.query):
            from src.agent.candidate_evidence_followup import hydrophobic_comparison
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            if hydrophobic_comparison(evidence, self.session_id) is not None:
                self.solubility_requested_smiles = [row['canonical_smiles'] for row in evidence['candidates']]
                self.resumed_named_admet = (f"预测 {self.solubility_requested_smiles[0]} 和 "
                    f"{self.solubility_requested_smiles[1]} 的 ADMET，重点关注水溶解度。")
        if self.resumed_named_admet is not None:
            self.contextual_property = True
            return self.resumed_named_admet
        if self.mixed_execution_query is not None:
            return self.mixed_execution_query
        if self.property_calculation_query is not None:
            return self.property_calculation_query
        if self.concept_calculation is not None:
            return self.concept_calculation[1]
        if self.concept_history is not None:
            return self.concept_history[0]
        pending = self.history[-1].get('property_clarification') if self.history else None
        if isinstance(pending, dict) and pending.get('session_id') == self.session_id:
            from rdkit import Chem
            candidate = self.query.strip()
            molecule = Chem.MolFromSmiles(candidate) if re.fullmatch(r'[A-Za-z0-9@+\-\[\]()\\/=#$%.:*]+', candidate) else None
            smiles = Chem.MolToSmiles(molecule) if molecule is not None else None
            if smiles in pending['candidates']:
                self.contextual_property = True
                return f"计算 {smiles} 的 {pending['metrics']}。"
        query = property_comparison.corrected_property_query(self.query, self.history)
        resolved = property_comparison.contextual_property_query(query, self.history, self.session_id)
        self.contextual_property = resolved != self.query
        return resolved

    def model_target_context(self):
        """Project accepted socket-local retrieval facts for ordinary explanations."""
        evidence = self.history[-1].get('target_structure_evidence') if self.history else None
        if (not isinstance(evidence, dict) or not self.session_id
                or evidence.get('session_id') != self.session_id or not evidence.get('trace_id')):
            return None
        fields = ('structure_id', 'target', 'uniprot_id', 'resolution', 'ligand_ids')
        statuses = {'retrieved': '已检索元数据；结构与准备状态尚未检查',
                    'stale': '记录已过期，需要重新检索', 'unverified': '本地记录尚未核验'}
        context = {'scope': 'last_target_lookup_metadata', 'structures': [
            {**{key: deepcopy(row[key]) for key in fields if key in row},
             '核验状态': statuses.get(row.get('record_status'), statuses['unverified'])}
            for row in evidence['structures']]}
        if evidence.get('selected_structure_id'):
            context['selected_structure_id'] = evidence['selected_structure_id']
        return context

    def model_candidate_context(self):
        """Project accepted calculation/prediction facts for ordinary explanations."""
        if not _PRIOR_RESULT_REFERENCE.search(self.query):
            return None
        evidence = self.history[-1].get('candidate_evidence') if self.history else None
        if (not isinstance(evidence, dict) or not self.session_id
                or evidence.get('session_id') != self.session_id or not evidence.get('trace_id')):
            return None
        from .decision_chat import _requested_admet_endpoints
        requested = {name.casefold() for name in _requested_admet_endpoints(evidence.get('query'))}
        rows = []
        for candidate in evidence['candidates']:
            row = {key: deepcopy(candidate[key]) for key in
                   ('canonical_smiles', 'properties', 'baseline_rank') if key in candidate}
            if 'admet' in candidate:
                admet = candidate['admet']
                row['admet'] = {
                    'endpoints': {name: deepcopy(value) for name, value in admet.get('endpoints', {}).items()
                                  if not requested or name.casefold() in requested},
                    'invalid_endpoints': [name for name in admet.get('invalid_endpoints', [])
                                          if not requested or name.casefold() in requested]}
            rows.append(row)
        return {'scope': 'last_candidate_observations', 'candidates': rows}

    def model_activity_context(self):
        """Keep accepted activity scope and disagreements attached to their rows."""
        if not _PRIOR_RESULT_REFERENCE.search(self.query):
            return None
        evidence = self.history[-1].get('activity_evidence') if self.history else None
        if (not isinstance(evidence, dict) or not self.session_id
                or evidence.get('session_id') != self.session_id or not evidence.get('trace_id')):
            return None
        rows = []
        for prediction in evidence['observations'][0]['data']:
            row = {key: deepcopy(prediction[key]) for key in (
                'smiles', 'requested_target', 'family_id', 'status', 'execution_status',
                'activity_class', 'activity_probability', 'predicted_pIC50', 'units',
                'label_threshold', 'probability_threshold', 'classification_regression_consistent',
                'errors', 'warnings') if key in prediction}
            provenance = prediction.get('provenance') or {}
            scope = provenance.get('scope', {})
            # Training-asset provenance is not the origin of this prediction.
            row['model_scope'] = {key: deepcopy(scope[key]) for key in ('target_scope', 'species') if key in scope}
            row['models'] = {
                task: {key: deepcopy(model[key]) for key in ('model_id', 'task_type', 'endpoint', 'units')
                       if key in model}
                for task, model in provenance.get('models', {}).items()
                if task in ('classification', 'regression')}
            rows.append(row)
        return {'scope': 'last_activity_observations', 'predictions': rows}

    def followup(self, *, enable_tools, enable_rag=False):
        if self.mixed_activity_discussion:
            return None
        from src.literature.model_sources import model_source_followup
        source_followup = model_source_followup(self.query, self.session_id, self.history,
                                                enable_tools=enable_tools)
        if source_followup is not None:
            prior = source_followup.pop('reused_evidence', None)
            if prior is not None:
                self.entry['candidate_evidence'] = deepcopy(prior)
            source_record = source_followup.pop('source_record', None)
            if source_record is not None:
                self.entry['model_source_evidence'] = {'session_id': self.session_id, **source_record}
            return source_followup
        from .assay_comparison import compare_supplied_ic50
        assay_comparison = compare_supplied_ic50(self.query)
        if assay_comparison is not None:
            return assay_comparison
        from .calibration_report import compare_supplied_probabilities
        calibration = compare_supplied_probabilities(self.query)
        if calibration is not None:
            return calibration
        standalone_ki = re.fullmatch(
            r'\s*假设酶抑制符合可逆竞争性模型[，,]\s*测得\s*IC50\s*(?:为|是)\s*'
            r'(?P<ic50>[+-]?\d+(?:\.\d+)?)\s*(?P<ic50_unit>pM|nM|[μµu]M|mM)[，,]\s*'
            r'底物浓度(?:为|是)\s*(?P<substrate>[+-]?\d+(?:\.\d+)?)\s*(?P<substrate_unit>pM|nM|[μµu]M|mM)[，,]\s*'
            r'Km\s*(?:为|是)\s*(?P<km>[+-]?\d+(?:\.\d+)?)\s*(?P<km_unit>pM|nM|[μµu]M|mM)[。.!！]\s*'
            r'请估算\s*Ki\s*并说明公式与适用限制[。.!！?？]?\s*', self.query, re.I)
        if standalone_ki:
            return _estimate_ki(standalone_ki['ic50'], standalone_ki['ic50_unit'], standalone_ki)
        ki_calculation = re.fullmatch(
            r'\s*补充[：:]\s*这次测定底物浓度(?:为|是)\s*(?P<substrate>[+-]?\d+(?:\.\d+)?)\s*'
            r'(?P<substrate_unit>pM|nM|[μµu]M|mM)[，,]\s*相同条件下\s*Km\s*(?:为|是)\s*'
            r'(?P<km>[+-]?\d+(?:\.\d+)?)\s*(?P<km_unit>pM|nM|[μµu]M|mM)[。.!！]\s*'
            r'假设符合简单可逆竞争性模型[，,]\s*请估算\s*Ki\s*并说明计算过程和限制[。.!！?？]?\s*',
            self.query, re.I)
        if ki_calculation:
            prior = self.history[-1].get('ic50_ki_consultation') if self.history else None
            if (not isinstance(prior, dict) or not self.session_id or prior.get('session_id') != self.session_id
                    or not isinstance(prior.get('ic50'), str) or not isinstance(prior.get('unit'), str)):
                return {'content': '无法核对本会话中用户提供的 IC50，请重新提供完整数据。', 'status': 'partial'}
            self.entry['ic50_ki_consultation'] = deepcopy(prior)
            return _estimate_ki(prior['ic50'], prior['unit'], ki_calculation)
        if re.fullmatch(r'\s*请继续说明需要哪些实验条件才能估算\s*Ki[，,]'
                        r'以及换算的适用前提[。.!！?？]?\s*', self.query, re.I):
            prior = self.history[-1].get('ic50_ki_consultation') if self.history else None
            if isinstance(prior, dict) and self.session_id and prior.get('session_id') == self.session_id:
                self.entry['ic50_ki_consultation'] = deepcopy(prior)
                return {'content': _IC50_KI_CONDITIONS, 'status': 'completed'}
        descriptor = property_comparison.recorded_descriptor(self.query, self.history, self.session_id)
        if descriptor is not None:
            evidence = descriptor.pop('reused_evidence', None)
            if evidence is not None:
                self.entry['property_evidence'] = deepcopy(evidence)
            return descriptor
        salt_question = re.fullmatch(
            r'\s*(?:刚才|上一轮)的分子量包含氯离子吗[？?]\s*'
            r'这个\s*log\s*p\s*能否直接作为游离碱的实验分配系数[？?。]?\s*', self.query, re.I)
        if salt_question:
            previous = self.history[-1] if self.history else {}
            evidence = previous.get('property_evidence')
            if (previous.get('agent_used') is not True or not isinstance(evidence, dict)
                    or not self.session_id or evidence.get('session_id') != self.session_id
                    or not evidence.get('trace_id') or not evidence.get('evidence_id')):
                return {'content': '无法核对上一轮的结构与性质记录，请重新计算后再确认。', 'status': 'partial'}
            from rdkit import Chem
            molecule = Chem.MolFromSmiles(evidence.get('canonical_smiles', ''))
            fragments = Chem.GetMolFrags(molecule, asMols=True) if molecule is not None else ()
            chloride = any(fragment.GetNumAtoms() == 1
                           and fragment.GetAtomWithIdx(0).GetAtomicNum() == 17
                           and fragment.GetAtomWithIdx(0).GetFormalCharge() == -1
                           for fragment in fragments)
            if (not chloride or len(fragments) < 2 or any(
                    type(evidence.get(key)) not in (int, float) or not math.isfinite(evidence[key])
                    for key in ('molecular_weight', 'logp'))):
                return {'content': '无法确认上一轮记录包含氯离子及对应的完整性质结果。', 'status': 'partial'}
            self.entry['property_evidence'] = deepcopy(evidence)
            return {'content': (f"上一轮分子量 {evidence['molecular_weight']:.2f} Da 包含输入结构中的氯离子；"
                    f"logP {evidence['logp']:.3f} 是对完整输入结构的计算值，"
                    '不能直接作为游离碱的实验分配系数。本轮未重新计算。'), 'status': 'completed'}
        origin_question = re.fullmatch(
            r'\s*(?:刚才|上一轮)的\s*log\s*p\s*是实验测得的吗[？?]\s*'
            r'这两个数值是怎么得到的[？?。]?\s*', self.query, re.I)
        if origin_question:
            previous = self.history[-1] if self.history else {}
            evidence = previous.get('property_evidence')
            if (previous.get('agent_used') is not True or not isinstance(evidence, dict)
                    or not self.session_id or evidence.get('session_id') != self.session_id
                    or not evidence.get('trace_id') or not evidence.get('evidence_id')
                    or any(type(evidence.get(key)) not in (int, float)
                           or not math.isfinite(evidence[key]) for key in ('molecular_weight', 'logp'))):
                return {'content': '无法核对上一轮的性质计算记录，请重新计算后查看来源。', 'status': 'partial'}
            self.entry['property_evidence'] = deepcopy(evidence)
            return {'content': (f"上一轮分子量为 {evidence['molecular_weight']:.2f} Da，"
                    f"logP 为 {evidence['logp']:.2f}。两者由 RDKit 根据输入结构计算；"
                    'logP 是结构估算值，不是实验测得的分配系数。本轮未重新计算。'),
                    'status': 'completed'}
        from src.agent.pdb_origin import answer_pdb_origin
        pdb_origin = answer_pdb_origin(self.query, enable_tools=enable_tools,
                                       history=self.history, session_id=self.session_id)
        if pdb_origin is not None:
            if pdb_origin.get('evidence') is not None:
                self.entry['pdb_origin_evidence'] = deepcopy(pdb_origin.pop('evidence'))
            return pdb_origin
        if self.invalid_named_admet_request is not None:
            self.entry['pending_named_admet'] = self.invalid_named_admet_request
            return {'content': '提供的 SMILES 无效，请检查并在下一条消息重新提供，'
                    '格式为“SMILES: 分子结构”；本次未进行预测。', 'status': 'rejected'}
        from src.agent.contracts.concept_request import _MEASURED_KD_CLARIFICATION
        kd = _MEASURED_KD_CLARIFICATION.fullmatch(self.query)
        if kd:
            from .assay_comparison import compare_supplied_kd
            previous = self.history[-1].get('affinity_question') if self.history else None
            return compare_supplied_kd(kd, previous, self.session_id)
        affinity_question = _AFFINITY_COMPARISON.fullmatch(self.query)
        inference = _AFFINITY_INFERENCE.fullmatch(self.query)
        question = affinity_question or inference
        if question:
            self.entry['affinity_question'] = {'session_id': self.session_id, **question.groupdict()}
            if not self.history:
                return {'content': '目前无法确认这两个靶点的亲和力比值。请提供化合物、两个靶点的测量值、指标及实验条件。',
                        'status': 'partial'}
        ratio_question = re.fullmatch(
            r'\s*(?:换个角度说[，,]\s*)?(?P<first>[A-Za-z0-9_-]+)\s*的\s*IC50\s*是\s*'
            r'(?P<second>[A-Za-z0-9_-]+)\s*的\s*(?P<ratio>\d+(?:\.\d+)?)\s*倍[，,]\s*对吗[？?。]?\s*', self.query, re.I)
        if affinity_question or ratio_question:
            from src.agent.contracts.concept_request import _MEASURED_SELECTIVITY
            from decimal import Decimal, localcontext
            saved = self.history[-1].get('measured_selectivity') if self.history else None
            measured = (_MEASURED_SELECTIVITY.fullmatch(saved.get('query', ''))
                        if isinstance(saved, dict) and self.session_id and saved.get('session_id') == self.session_id else None)
            question = affinity_question or ratio_question
            if (not measured or {question['first'].casefold(), question['second'].casefold()}
                    != {measured['target'].casefold(), measured['off'].casefold()}):
                return {'content': '无法确认本会话对应的两个实测 IC50；请重新提供数据和靶点。', 'status': 'partial'}
            scales = {'pm': Decimal('1e-12'), 'nm': Decimal('1e-9'),
                      'μm': Decimal('1e-6'), 'um': Decimal('1e-6'), 'mm': Decimal('1e-3')}
            target = Decimal(measured['target_value']) * scales[measured['target_unit'].casefold()]
            off = Decimal(measured['off_value']) * scales[measured['off_unit'].casefold()]
            if target <= 0 or off <= 0:
                return {'content': 'IC50 必须为正数，无法根据所给数值计算比值；请检查实验数据。', 'status': 'partial'}
            self.entry['measured_selectivity'] = deepcopy(saved)
            with localcontext() as ctx:
                ctx.prec = 6
                forward, reverse = off / target, target / off
            if affinity_question:
                return {'content': f'不能仅凭 IC50 比值确定结合亲和力相差多少。根据你提供的数据，'
                        f'{measured["off"]} 的 IC50 ÷ {measured["target"]} 的 IC50 = {forward:g} 倍；'
                        '还需确认测定条件可比，并使用适当的结合实验验证。', 'status': 'partial'}
            actual = reverse if question['first'].casefold() == measured['target'].casefold() else forward
            verdict = '是' if actual == Decimal(question['ratio']) else '不是'
            return {'content': f'{verdict}。根据你提供的数据，{measured["target"]} 的 IC50 ÷ '
                    f'{measured["off"]} 的 IC50 = {reverse:g} 倍；反向的 {measured["off"]} ÷ '
                    f'{measured["target"]} = {forward:g} 倍。解读选择性时还需确认测定条件可比。',
                    'status': 'partial'}
        if re.fullmatch(r'\s*(?:没找到|未找到|没有|缺少)(?:可用的?)?模型(?:是否|是不是|能否)'
                        r'(?:意味着|说明|证明)(?:这个|该)分子对(?:人|人源)?\s*'
                        r'[A-Za-z][A-Za-z0-9_-]{0,31}\s*(?:没有|无)(?:抑制)?活性[？?。]?\s*', self.query, re.I):
            return {'content': '没有可用模型不能说明该分子有或没有靶点活性，也不能推断活性强弱；'
                    '需要针对相应靶点和物种进行实验验证。本次未重新预测。', 'status': 'partial'}
        generation_question = re.fullmatch(
            r'\s*这(?:是否|能否|能不能)证明(?:该|这个)分子(?:能|可以)?抑制\s*'
            r'(?P<target>[A-Za-z][A-Za-z0-9_-]{0,31})[？?。]?\s*', self.query, re.I)
        if generation_question:
            from src.target_identifiers import canonical_target_identifier
            evidence = self.history[-1].get('generated_candidate_evidence') if self.history else None
            if (not isinstance(evidence, dict) or not self.session_id or evidence.get('session_id') != self.session_id
                    or evidence.get('target') != canonical_target_identifier(generation_question['target'])):
                return {'content': '无法唯一核对当前会话中该靶点的生成候选；请重新选择候选并说明靶点。', 'status': 'partial'}
            self.entry['generated_candidate_evidence'] = deepcopy(evidence)
            return {'content': f"不能。`{evidence['smiles']}` 是生成的候选结构，生成本身不能证明它抑制该靶点。"
                    '本次生成未进行活性预测；需要针对相应靶点和物种的实验验证。', 'status': 'partial'}
        from src.agent.contracts.docking_intent import docking_preparation_consultation
        if docking_preparation_consultation(self.query) or re.fullmatch(
            r'\s*(?:我想|我计划|我准备)(?:把|将|使用)\s*(?:'
            r'SMILES\s*[:：]\s*[A-Za-z0-9@+\-\[\]()=#\\/%.:*]{1,512}'
            r'\s*(?:与|和)\s*[A-Za-z][A-Za-z0-9_-]{0,31}\s*的\s*PDB\s*[0-9][A-Za-z0-9]{3}'
            r'|PDB\s*[0-9][A-Za-z0-9]{3}\s*(?:与|和)\s*(?:配体\s*)?'
            r'SMILES\s*[:：]\s*[A-Za-z0-9@+\-\[\]()=#\\/%.:*]{1,512})'
            r'\s*(?:进行|做)?(?:分子)?对接[，,]\s*(?:(?:但)?口袋(?:尚未|没有|未)确定'
            r'|(?:但)?(?:现在|目前)?(?:还没有|尚未|没有|未)确定口袋)[。.!！]'
            r'\s*请(?:先)?(?:(?:告诉我|说明)(?:还)?需要哪些输入和准备|说明开始之前需要准备什么)'
            r'[，,]\s*(?:暂时)?不要(?:执行|运行)对接[。.!！]?\s*',
            self.query, re.I):
            return {'content': _DOCKING_PREPARATION_ADVICE, 'status': 'partial'}
        from src.agent.contracts.target_request import UNSUPPORTED_TARGET_SPECIES, unrecognized_lookup_qualifier
        unsupported_target_species = re.fullmatch(
            rf'\s*(?:请)?(?:查询|检索|查找|搜索)\s*({UNSUPPORTED_TARGET_SPECIES}|[\u4e00-\u9fff]{{1,8}}源)\s*'
            r'[A-Za-z][A-Za-z0-9_-]*\s*的(?:蛋白信息和)?(?:实验)?(?:蛋白)?结构'
            r'(?:[，,]\s*列出物种[、,，]\s*UniProt\s*(?:和|与|及)\s*PDB\s*编号)?[。.!！?？]?\s*',
            self.query, re.I)
        if unsupported_target_species and unsupported_target_species[1] not in ('人源', '小鼠源'):
            return {'content': f'当前靶点检索暂不支持{unsupported_target_species[1]}物种；'
                    '本次未检索，也未使用人源结果替代。', 'status': 'rejected'}
        declared_species = re.fullmatch(
            r'\s*(?:请)?(?:查询|检索|查找|搜索)\s*[A-Za-z][A-Za-z0-9_-]*\s*'
            r'的(?:蛋白信息和)?(?:实验)?(?:蛋白)?结构\s*[，,]\s*(?:靶点)?物种\s*'
            r'(?:为|是|[:：=])\s*([\u4e00-\u9fffA-Za-z -]{1,48})\s*[。.!！?？]?\s*',
            self.query, re.I)
        if declared_species:
            from src.agent.tools.activity_input import requested_activity_species
            try:
                species = requested_activity_species(f'物种为{declared_species[1].strip()}')
            except ValueError:
                species = None
            if species not in ('human', 'mouse'):
                return {'content': f'当前靶点检索仅支持人源和小鼠，无法按{declared_species[1].strip()}'
                        '物种查询；本次未检索。', 'status': 'rejected'}
        qualifier = unrecognized_lookup_qualifier(self.query)
        if qualifier and re.fullmatch(
            rf'\s*(?:请)?(?:查询|检索|查找|搜索)\s*{re.escape(qualifier)}\s+'
            r'[A-Za-z][A-Za-z0-9_-]*\s*的(?:蛋白信息和)?(?:实验)?(?:蛋白)?结构'
            r'(?:[，,]\s*列出物种[、,，]\s*UniProt\s*(?:和|与|及)\s*PDB\s*编号)?[。.!！?？]?\s*',
            self.query, re.I):
            return {'content': f'无法确认{qualifier}对应的靶点物种；当前靶点检索仅支持人源和小鼠。'
                    '请核对物种后重试，本次未检索。', 'status': 'rejected'}
        from src.agent.planning.request_parsing import positive_intent_text
        from src.agent.contracts.concept_request import is_oral_pk_difference
        if is_oral_pk_difference(self.query):
            # FDA clinical pharmacology review uses dose-normalized oral/IV AUC:
            # https://www.accessdata.fda.gov/drugsatfda_docs/nda/2014/206619Orig1s000ClinPharmR.pdf
            return {'content': '口服吸收描述药物从胃肠道跨越肠壁进入体内的过程或比例；'
                    '绝对口服生物利用度描述口服剂量中以原形药物到达体循环的比例。'
                    '吸收后，肠壁和肝脏等部位的首过消除会使进入体循环的原形药量减少；'
                    '因此吸收良好并不保证生物利用度高。生物利用度本身不表示药物到达作用部位的比例。'
                    '绝对生物利用度通常通过剂量校正的口服与静脉原形药物 AUC 比较估计。',
                    'status': 'completed'}
        positive = positive_intent_text(self.query)
        if _bioavailability_comparison(positive):
            return {'content': _BIOAVAILABILITY_UNAVAILABLE, 'status': 'not_calculated'}
        literature_keywords = re.fullmatch(
            r'\s*(?:请)?(?:检索|搜索|查询)文献\s*[:：]\s*([A-Za-z0-9][A-Za-z0-9 ._+()"-]{0,255})\s*',
            self.query, re.I)
        if literature_keywords:
            if not enable_tools:
                return {'content': '当前工具未启用，本次未检索文献。', 'status': 'not_calculated'}
            from src.literature.europe_pmc import EuropePMCClient
            from src.target_search.remote_clients import BoundedJsonClient, RemoteSourceError
            try:
                with BoundedJsonClient(operation_timeout=15, timeout=(3, 7), max_attempts=2) as http:
                    result = EuropePMCClient(http).search(literature_keywords[1].strip())
            except RemoteSourceError:
                return {'content': '文献检索暂不可用，请稍后重试。', 'status': 'partial'}
            from src.system.scientific_public_data import public_scientific_data
            from src.literature.summary_evidence import prepare_abstract
            lines = [f"Europe PMC 返回 {result['hit_count']} 条匹配记录，以下为已返回的 PubMed 条目："]
            stored = []
            for index, article in enumerate(result['articles'], 1):
                title = article['title']
                if public_scientific_data(title)[1]:
                    title = f"PubMed {article['pmid']}（标题请查看原文）"
                title = html.escape(title).replace('[', '［').replace(']', '］').replace('`', r'\`')
                year = article['year'] or '年份未返回'
                kinds = '、'.join(html.escape(value).replace('[', '［').replace(']', '］').replace('`', r'\`')
                                 for value in article['publication_types']) or '类型未返回'
                if public_scientific_data(kinds)[1]:
                    kinds = '类型请查看原文'
                lines.append(f"{index}. [{title}]({article['url']})（{year}；{kinds}）")
                abstract = article.get('abstract') or ''
                words = abstract.split()
                excerpt = ' '.join(words[:20]) + ('…' if len(words) > 20 else '')
                if public_scientific_data(excerpt)[1]:
                    excerpt = ''
                stored.append({'pmid': article['pmid'], 'title': title, 'year': year, 'types': kinds,
                               'url': article['url'], 'excerpt': excerpt,
                               'summary_source': prepare_abstract(article)})
            if not result['articles']:
                lines.append('本次未返回可核验的 PubMed 条目。')
            self.entry['literature_evidence'] = {'session_id': self.session_id, 'articles': stored}
            lines.append('检索匹配不代表文章支持某个具体结论；需核对研究类型、内容及实验条件。')
            return {'content': '\n'.join(lines), 'status': 'completed'}
        fulltext_request = re.fullmatch(
            r'\s*(?:请)?(?:获取|查阅|检索|查找|分析|阅读|解读|总结)\s*PMID\s*[:：]?\s*'
            r'([1-9][0-9]{0,11})\s*的(?:开放|论文)?全文(?:[，,。；;？?][\s\S]{0,600})?\s*',
            self.query, re.I)
        if fulltext_request:
            if (len(re.findall(r'(?<![A-Za-z0-9_])PMID\s*[:：]?\s*[0-9]+', self.query, re.I)) != 1
                    or re.search(r'(?:[，,。；;？?\n]|然后|并且|同时)\s*'
                        r'(?:(?:请|再|并|然后|帮我|帮忙|给我|继续)\s*)*(?:计算|预测|运行|执行|生成|设计|对接)\s*',
                        positive, re.I)):
                return None
            if not enable_tools:
                return {'content': '当前工具未启用，无法检索开放全文。', 'status': 'partial'}
            return {'content': '开放全文暂不可用，尚未核对正文。', 'status': 'partial',
                    'fulltext_pmid': fulltext_request[1], 'fulltext_question': self.query}
        previous = self.history[-1] if self.history else None
        evidence = previous.get('literature_fulltext') if isinstance(previous, dict) else None
        fulltext_followup = (len(self.query) <= 300
            and (isinstance(evidence, dict) or re.search(r'论文|全文|文章|文献', self.query))
            and not re.match(r'\s*(?:请)?(?:分析|阅读|解读|总结)\s*PMID\b', self.query, re.I)
            and re.search(r'这(?:两|些|个|篇)|上述|前述|刚才|该论文|论文中', self.query)
            and re.search(r'\bK[di]\b|抑制常数|结合常数|数值|实验条件|比较组', self.query, re.I))
        if fulltext_followup:
            if re.search(r'(?:^|[。；;，,？?])\s*(?:(?:请|再|并|然后)\s*)*'
                         r'(?:计算|预测|运行|执行|生成|设计|对接)\s*', self.query, re.I):
                return None
            if (not isinstance(evidence, dict) or not self.session_id or evidence.get('session_id') != self.session_id
                    or not isinstance(evidence.get('pmid'), str) or not re.fullmatch(r'[1-9][0-9]{0,11}', evidence['pmid'])
                    or not isinstance(evidence.get('pmcid'), str) or not re.fullmatch(r'PMC[1-9][0-9]{0,11}', evidence['pmcid'])
                    or not isinstance(evidence.get('passages'), list) or not evidence['passages']):
                return {'content': '缺少本会话可核对的全文证据，请先检索论文全文。', 'status': 'partial'}
            if not enable_tools:
                return {'content': '当前工具未启用，无法重新核对论文正文。', 'status': 'partial'}
            origin = evidence.get('source_question') or previous.get('user')
            if not isinstance(origin, str) or not origin:
                return {'content': '缺少原始全文问题，无法安全定位正文，请重新检索。', 'status': 'partial'}
            return {'content': '全文证据暂不可用，尚未核对正文。', 'status': 'partial',
                    'fulltext_pmid': evidence['pmid'], 'fulltext_question': origin + '\n当前追问：' + self.query,
                    'fulltext_source_question': origin, 'fulltext_previous': deepcopy(evidence)}
        research_pmid_followup = re.fullmatch(
            r'\s*(?:请)?(?:概括|总结|解读|分析)\s*PMID\s*[:：]?\s*([1-9][0-9]{0,11})\s*的摘要'
            r'(?:[，,][^。；;\n]{1,160})?[。？?]?\s*', self.query, re.I)
        composite_pmid = re.fullmatch(
            r'\s*(?:请)?(?:查询|检索|查找)\s*PMID\s*[:：]?\s*([1-9][0-9]{0,11})'
            r'\s*的(?:论文)?题目[、,，]\s*DOI\s*(?:和|与|及)摘要[，,]\s*'
            r'(?:并)?(?:概括|总结|分析)摘要[^。；;\n]{1,120}'
            r'(?:[；;]\s*没有核验的信息请明确说明)?[。？?]?\s*', self.query, re.I)
        if composite_pmid:
            if re.search(r'计算|预测|运行|执行|生成|设计|对接', self.query):
                return None
            research_pmid_followup = composite_pmid
        if research_pmid_followup:
            if re.search(r'[，,；;。]\s*(?:(?:请|再|并|然后)\s*)*(?:计算|预测|运行|执行|生成|设计|对接)\s*',
                         self.query, re.I):
                return None
            evidence = self.history[-1].get('literature_research') if self.history else None
            from src.literature.summary_evidence import prepare_abstract
            sources = ([source for attempt in evidence.get('attempts', []) if isinstance(attempt, dict)
                        for source in attempt.get('sources', []) if isinstance(source, dict)
                        and source.get('pmid') == research_pmid_followup[1] and prepare_abstract(source) == source]
                       if isinstance(evidence, dict) and self.session_id and evidence.get('session_id') == self.session_id
                       else [])
            if not sources or composite_pmid:
                if not enable_tools:
                    return {'content': '当前工具未启用，无法检索该 PMID 的摘要。', 'status': 'partial'}
                return {'content': '文献检索暂不可用，尚未核对摘要。', 'status': 'partial',
                        'research_lookup_pmid': research_pmid_followup[1], 'source_research_question': self.query,
                        'research_metadata_requested': bool(composite_pmid)}
            source = sources[0]
            self.entry['literature_research'] = deepcopy(evidence)
            return {'content': research_abstract_message(source, self.query), 'status': 'partial',
                    'research_summary_source': source, 'source_research_question': self.query}
        research_ordinal = re.fullmatch(
            r'\s*(?:请)?第([一二三四五六七八九十1-9]|10)篇(?:论文|文献)?的摘要'
            r'(?:说了什么|讲了什么|是什么|研究了什么)[？?。]?\s*', self.query)
        if research_ordinal:
            evidence = self.history[-1].get('literature_research') if self.history else None
            if not isinstance(evidence, dict) or not self.session_id or evidence.get('session_id') != self.session_id:
                return {'content': '缺少本会话可核对的文献列表，请先检索文献。', 'status': 'partial'}
            candidates = evidence.get('candidates', [])
            ordinal = research_ordinal[1]
            index = int(ordinal) if ordinal.isdigit() else '一二三四五六七八九十'.index(ordinal) + 1
            if not isinstance(candidates, list) or not 1 <= index <= len(candidates):
                return {'content': '上一轮没有对应序号的文献，请检查列表。', 'status': 'partial'}
            candidate = candidates[index - 1]
            pmid = candidate['pmid']
            from src.literature.summary_evidence import prepare_abstract
            sources = [source for attempt in evidence.get('attempts', []) for source in attempt.get('sources', [])
                       if source.get('pmid') == pmid and prepare_abstract(source) == source]
            if not sources:
                return {'content': f'第 {index} 篇的摘要不可用，尚未生成研究结论。', 'status': 'partial'}
            self.entry['literature_research'] = deepcopy(evidence)
            title = html.escape(candidate['title']).replace('[', '［').replace(']', '］').replace('`', r'\`')
            heading = f'第 {index} 篇：{title}（PMID {pmid}）。\n\n'
            return {'content': heading + research_abstract_message(sources[0], self.query), 'status': 'partial',
                    'research_summary_source': sources[0], 'source_research_question': self.query}
        literature_followup = re.fullmatch(
            r'\s*第([一二三四五1-5])篇(?:是什么类型的文章|是什么类型)[？?]'
            r'\s*它的摘要研究了什么[？?。]?\s*', self.query)
        if literature_followup:
            evidence = self.history[-1].get('literature_evidence') if self.history else None
            if not isinstance(evidence, dict) or not self.session_id or evidence.get('session_id') != self.session_id:
                return {'content': '缺少本会话可核对的文献列表，请先检索文献。', 'status': 'partial'}
            articles = evidence.get('articles')
            digit = literature_followup[1]
            index = int(digit) if digit.isdigit() else '一二三四五'.index(digit) + 1
            if not isinstance(articles, list) or not 1 <= index <= len(articles):
                return {'content': '上一轮没有对应序号的文献，请检查列表。', 'status': 'partial'}
            article = articles[index - 1]
            excerpt = html.escape(article['excerpt']).replace('[', '［').replace(']', '］').replace('`', r'\`')
            detail = f'摘要摘录：{excerpt}' if excerpt else '摘要摘录不可用，请查看原文。'
            self.entry['literature_evidence'] = deepcopy(evidence)
            heading = f"第 {index} 篇：[{article['title']}]({article['url']})（{article['year']}；{article['types']}）。"
            return {'content': heading + detail + ' 本次未自动总结全文；发表类型以数据库记录为准。',
                    'status': 'partial', 'summary_source': article.get('summary_source'), 'summary_heading': heading}
        literature_requested = bool(re.search(r'文献|论文|期刊|\b(?:literature|papers?|publications?)\b', positive, re.I))
        if literature_requested and re.search(
                r'(?:^|[。；;，,]|并|再|然后)\s*(?:(?:请|再|并|然后)\s*)*'
                r'(?:计算|预测|运行|执行|生成|设计|对接)\s*',
                self.query, re.I):
            return None
        literature_research = re.fullmatch(
            r'\s*(?:请)?(?:检索|搜索|查找)(?:相关原始研究|原始研究|原始|相关)?'
            r'(?:文献|论文)[，,]\s*(?:分析|解释|说明)[^。；;\n]{1,300}[。]?[\s]*', self.query, re.I)
        subject_first_research = re.fullmatch(
            r'\s*(?:请)?(?:检索|搜索|查找)\s*[^。；;，,\n]{1,200}(?:文献|论文)[，,]\s*'
            r'列出(?:论文)?题目[、,，]\s*年份[、,，]\s*PMID\s*(?:和|与|及)来源[，,]\s*'
            r'(?:并)?(?:概括|总结|说明)摘要支持的(?:主要)?结论'
            r'(?:[；;]\s*(?:没有|未)核验的实验数值请明确说明)?[。.!！]?\s*', self.query, re.I)
        summary_research = re.fullmatch(
            r'\s*(?:请)?(?:检索|搜索|查找|查询)\s*[^。；;，,\n]{1,200}(?:文献|论文)'
            r'(?:[，,]\s*|并\s*)(?:并)?(?:总结|概括|分析|梳理)'
            r'(?:这些|其|论文的)?摘要(?:中的|中|的)?(?:主要|关键|相关|实验|研究)*'
            r'(?:发现|结果|结论|内容)'
            r'(?:[，,]\s*(?:并)?(?:附上|提供|列出)\s*(?:论文)?'
            r'(?:题目|标题|年份|PMID|DOI|来源|出处|链接)'
            r'(?:\s*(?:和|与|及|、|，|,)\s*(?:论文)?(?:题目|标题|年份|PMID|DOI|来源|出处|链接))*)?'
            r'[。.!！]?\s*', self.query, re.I)
        if literature_research or subject_first_research or summary_research:
            if not enable_tools:
                return {'content': '当前工具未启用，本次未检索文献。', 'status': 'not_calculated'}
            return {'content': '文献证据暂不可用，本次未生成有来源的结论。', 'status': 'partial',
                    'research_question': self.query, 'primary_only': '原始' in self.query}
        if (literature_requested
                and re.search(r'检索|搜索|查找|查询|给出|列出|提供|来源|出处|引用|'
                              r'\b(?:search|find|retrieve|cite|references?|sources?)\b', positive, re.I)):
            return {'content': '目前尚不能自动检索并分析文献回答这一问题，本次未执行文献检索。'
                    '文献检索格式：检索文献：EGFR T790M ATP。'
                    '也可输入“总结 PMID”后加文献编号，再加“的摘要”。',
                    'status': 'not_calculated'}
        if self.concept_history is not None:
            return None  # The concept still needs an ordinary model response.
        from src.agent.candidate_evidence_followup import (endpoint_explanation_followup,
            ranking_explanation_followup, hydrophobic_comparison)
        solubility_followup = re.fullmatch(
            r'\s*(?:根据|基于)(?:刚才|上一轮|之前)的(?:结果|计算|性质)[，,]\s*'
            r'(?:能|可以|能否|是否能)?(?:判断|确定)(?:这两个分子中)?(?:哪个|哪一个)分子?'
            r'(?:在水中|的水|的水中)?的?溶解度(?:更高|较高)吗?[？?。]'
            r'(?:\s*(?:为什么|请说明原因|请解释原因)[？?。]?)?\s*', self.query)
        if solubility_followup:
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            if hydrophobic_comparison(evidence, self.session_id) is None:
                return {'content': '缺少当前会话中两个可核对的性质结果，请先计算分子性质。',
                        'status': 'not_calculated'}
            self.entry['candidate_evidence'] = deepcopy(evidence)
            rows = evidence['candidates']
            values = '；'.join(f"`{row['canonical_smiles']}` 的计算 logP 为 {row['properties']['logp']:.6g}"
                              for row in rows)
            return {'content': values + '。仅凭这些计算描述符不能确定两者的水溶解度排序；'
                    '还需要相同 pH、温度及固态形式等条件下的实测溶解度，或可核验的预测结果。'
                    '本次未重新计算或预测溶解度。', 'status': 'partial'}
        hydrophobic_followup = re.fullmatch(
            r'\s*(?:这两个分子|这两者|这两个候选|它们)(?:中)?(?:哪一个|哪个)更(?:疏水|亲脂)[？?。]'
            r'(?:\s*(?:请)?(?:基于|根据)(?:刚才|上一轮|之前)的(?:计算结果|结果|计算)'
            r'(?:说明|解释)(?:原因|依据)[。.!！]?)?\s*', self.query)
        if hydrophobic_followup:
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            comparison = hydrophobic_comparison(evidence, self.session_id)
            if comparison is None:
                return {'content': '缺少当前会话中两个可核对的计算 logP，请先计算分子性质。',
                        'status': 'not_calculated'}
            self.entry['candidate_evidence'] = deepcopy(evidence)
            return {'content': comparison + '本次未重新计算。',
                    'status': 'partial' if '舍入精度不足' in comparison else 'completed'}
        ranking_view = ranking_explanation_followup(self.query, self.history, self.session_id)
        if ranking_view is not None:
            if ranking_view.get('reused_evidence') is not None:
                self.entry['candidate_evidence'] = deepcopy(ranking_view.pop('reused_evidence'))
            return ranking_view
        endpoint_view = endpoint_explanation_followup(self.query, self.history, self.session_id)
        if endpoint_view is not None:
            if endpoint_view.get('reused_evidence') is not None:
                self.entry['candidate_evidence'] = deepcopy(endpoint_view.pop('reused_evidence'))
            return endpoint_view
        batch_lipinski = property_comparison.batch_lipinski_followup(self.query, self.history, self.session_id)
        if batch_lipinski is not None:
            if batch_lipinski.get('pending') is not None:
                self.entry['batch_lipinski_pending'] = deepcopy(batch_lipinski.pop('pending'))
            if batch_lipinski.get('reused_evidence') is not None:
                self.entry['batch_property_evidence'] = deepcopy(batch_lipinski.pop('reused_evidence'))
                self.entry['batch_property_evidence_reused'] = True
            return batch_lipinski
        batch_logp = property_comparison.batch_logp_followup(self.query, self.history, self.session_id)
        if batch_logp is not None:
            if batch_logp.get('reused_evidence') is not None:
                self.entry['batch_property_evidence'] = deepcopy(batch_logp.pop('reused_evidence'))
                self.entry['batch_property_evidence_reused'] = True
            return batch_logp
        source_status_question = re.fullmatch(
            r'反向寻靶数据源的校验(?:现在)?进行到哪里[？?]\s*还要等多久[？?。]?\s*', self.query)
        if source_status_question:
            tool = getattr(self.reverse_boundary, 'reverse_tool', None)
            if not callable(getattr(tool, 'source_status', None)):
                return {'content': '反向寻靶数据源状态暂不可用，无法估计剩余时间。', 'status': 'partial'}
            status = tool.source_status()
            if status['state'] == 'loading':
                verified, total = status['verified_rows'], status['total_rows']
                progress = f'已校验 {verified}/{total} 条。' if total else '正在准备校验。'
                return {'content': f'反向寻靶数据源仍在校验。{progress}无法预计剩余时间；校验完成后可重新发起预测。',
                        'status': 'partial'}
            if status['state'] == 'ready':
                return {'content': '反向寻靶数据源校验已完成，可以重新发起预测；本次未运行预测。',
                        'status': 'completed'}
            return {'content': '反向寻靶数据源暂不可用，无法预计恢复时间；本次未运行预测。',
                    'status': 'partial'}
        two_endpoint_screen = re.fullmatch(
            r'根据(?:刚才|上一轮)(?:的)?结果[，,]\s*只按\s*DILI\s*(?:和|与)\s*hERG\s*'
            r'两项预测值都较低来筛选[，,]\s*(?:哪个|哪一个)候选更优先[？?]'
            r'\s*不要把预测当作人体安全性结论[。.!！?？]?\s*', self.query, re.I)
        safety_comparison = re.fullmatch(
            r'根据(?:刚才|上一轮)(?:的)?结果[，,]\s*(?:哪个|哪一个)(?:分子|候选)更安全[，,]'
            r'\s*(?:可以|能否|能)优先用于人体吗[？?。]?\s*', self.query, re.I) or re.fullmatch(
            r'根据(?:刚才|上一轮)(?:的)?\s*(?:hERG\s*(?:和|与)\s*DILI|DILI\s*(?:和|与)\s*hERG)'
            r'\s*预测[，,]\s*(?:哪个|哪一个)(?:分子|候选)更安全[？?]'
            r'\s*(?:能|可以|能否)直接据此选择临床用药吗[？?。]?\s*', self.query, re.I)
        experiment_priority = re.fullmatch(
            r'根据(?:刚才|上一轮)(?:的)?(?:预测|结果)[，,]\s*(?:哪个|哪一个)(?:分子|候选)?'
            r'更值得优先做实验[？?]\s*为什么[，,]还需要哪些实验[？?。]?\s*', self.query)
        if two_endpoint_screen or safety_comparison or experiment_priority:
            if experiment_priority:
                prior_query = self.history[-1].get('user', '') if self.history else ''
                if not (isinstance(prior_query, str) and re.search(r'\bhERG\b', prior_query, re.I)
                        and re.search(r'\bDILI\b', prior_query, re.I)):
                    return {'content': '请明确实验目标和需要比较的预测指标，再确定优先顺序。', 'status': 'partial'}
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            if (not self.session_id or not isinstance(evidence, dict) or evidence.get('session_id') != self.session_id
                    or not isinstance(rows, list) or len(rows) < 2
                    or any(not isinstance(row, dict) or not isinstance(row.get('canonical_smiles'), str)
                           or not any(isinstance(source, dict) and source.get('tool_name') == 'admet_predictor'
                                      and source.get('evidence_id') for source in row.get('sources', []))
                           for row in rows)):
                return {'content': '无法核对上一轮完整候选及 ADMET 记录，请先比较候选。',
                        'status': 'not_calculated'}
            lines = ['| 候选 SMILES | DILI 分类预测值 | hERG 分类预测值 |', '|---|---:|---:|']
            scores, missing = [], False
            for row in rows:
                endpoints = row.get('admet', {}).get('endpoints', {})
                values = []
                for name in ('DILI', 'hERG'):
                    item = endpoints.get(name)
                    value = item.get('value') if isinstance(item, dict) else None
                    if (not isinstance(item, dict) or item.get('task_type') != 'classification'
                            or item.get('unit') != '-' or type(value) not in (int, float)
                            or not math.isfinite(value) or not 0 <= value <= 1):
                        value = None
                        missing = True
                    values.append(value)
                scores.append(values)
                lines.append(f"| `{row['canonical_smiles']}` | "
                             + ' | '.join(f'{value:.4g}' if value is not None else '未返回' for value in values) + ' |')
            if experiment_priority:
                if missing:
                    lines.append('部分端点未返回，不能完成这些预测值的比较。')
                elif len(scores) == 2 and (scores[0][0] - scores[1][0]) * (scores[0][1] - scores[1][1]) < 0:
                    lines.append('两项预测分数的方向存在权衡；未指定实验目标和取舍标准，不能确定唯一优先分子。')
                else:
                    lines.append('仅凭这两项模型预测不能确定实验优先级，还需明确筛选目标并结合其他证据。')
                lines.append('可针对 hERG 开展通道抑制实验，并用合适的肝细胞模型评估肝毒性；'
                             '同时结合靶点活性、暴露和药代数据确定后续实验顺序。')
            elif safety_comparison:
                if missing:
                    lines.append('部分端点未返回，不能完成这些预测值的比较。')
                elif len(scores) == 2 and (scores[0][0] - scores[1][0]) * (scores[0][1] - scores[1][1]) < 0:
                    lines.append('两项预测分数的方向存在权衡，不能仅凭这两项确定整体安全性。')
                lines.append('这些分类预测不能确定哪个分子整体更安全，也不能证明可以用于人体；'
                             '还需评估模型适用域、暴露、药代和毒理，并通过相应实验与安全性评价。')
            elif missing:
                lines.append('至少一个候选缺少可核对的端点，不能完成两项比较。')
            else:
                frontier = [i for i, values in enumerate(scores) if not any(
                    all(a <= b for a, b in zip(other, values)) and any(a < b for a, b in zip(other, values))
                    for other in scores)]
                if len(frontier) == 1:
                    lines.append(f"{rows[frontier[0]]['canonical_smiles']} 在两项预测值上均不高于其他候选，"
                                 '可按所给规则优先复核。')
                elif all(scores[i] == scores[frontier[0]] for i in frontier):
                    lines.append('前列候选的两项预测值相同，按所给规则并列。')
                else:
                    names = '、'.join(rows[i]['canonical_smiles'] for i in frontier)
                    lines.append(f'候选 {names} 的两项预测值存在权衡；未给出取舍权重，无法确定唯一优先项。')
            lines.append('这是模型预测的筛选比较，不能证明人体安全性；还需适用域评估和相关实验。未重新预测。')
            self.entry['candidate_evidence'] = deepcopy(evidence)
            return {'content': '\n'.join(lines), 'status': 'partial' if missing or safety_comparison or experiment_priority else 'completed'}
        priority_question = re.fullmatch(
            r'根据(?:刚才|上一轮)的结果[，,]\s*哪(?:一)?个候选值得优先做\s*'
            r'(?P<target>[A-Za-z][A-Za-z0-9-]*)\s*实验[？?]'
            r'\s*(?:请)?比较依据和不确定性[，,]\s*不要把预测当作实验结果[。.!！?？]?\s*',
            self.query, re.I)
        if priority_question:
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            from src.agent.candidate_evidence_followup import _canonical
            from src.agent.contracts.target_request import analyze_target_request
            from src.target_identifiers import canonical_target_identifier
            prior_target = analyze_target_request(evidence.get('query', '') if isinstance(evidence, dict) else '')
            target_matches = (not prior_target.needs_clarification
                              and canonical_target_identifier(priority_question.group('target')) == prior_target.targets[0])
            if (not self.session_id or not isinstance(evidence, dict) or evidence.get('session_id') != self.session_id
                    or evidence.get('generation_scope_verified') is not True or not target_matches
                    or not isinstance(rows, list) or not rows
                    or any(not isinstance(row, dict) or not isinstance(row.get('properties'), dict)
                           or not isinstance(row.get('canonical_smiles'), str)
                           or not any(source.get('tool_name') == 'property_calculator' and source.get('evidence_id')
                                      for source in row.get('sources', [])) for row in rows)):
                return {'content': '无法核对上一轮完整候选、性质或所问靶点；请先生成候选并完成对应计算。',
                        'status': 'not_calculated'}
            lines = ['现有证据不足以确定唯一优先候选。以下比较复用上一轮已核对的分子性质和 ADMET 记录：',
                     '', '| 候选 SMILES | 分子量 (Da) | logP | DILI 分类预测 | hERG 分类预测 |',
                     '|---|---:|---:|---:|---:|']
            for row in rows:
                props = row['properties']
                endpoints = row.get('admet', {}).get('endpoints', {})
                values = []
                for name in ('DILI', 'hERG'):
                    item = endpoints.get(name)
                    values.append(f"{item['value']:.4g}" if isinstance(item, dict)
                                  and item.get('task_type') == 'classification' else '未返回')
                lines.append(f"| `{row['canonical_smiles']}` | {props['molecular_weight']:.2f} | "
                             f"{props['logp']:.3f} | {values[0]} | {values[1]} |")
            saved_activity = evidence.get('generated_activity_evidence')
            checked_activity = None
            if isinstance(saved_activity, dict):
                checked_activity = evidence_followup.accepted_generated_activity_evidence(
                    {'trace_id': saved_activity.get('trace_id'),
                     'tool_result_sequence': saved_activity.get('observations')},
                    evidence, evidence.get('query', ''), self.session_id)
            if checked_activity:
                activity_rows = {_canonical(row['smiles']): row
                                 for row in checked_activity['observations'][0]['data']}
                lines.extend(['', '| 候选 SMILES | 预测 pIC50 | 活性概率 | 分类与回归 | 模型物种范围 |',
                              '|---|---:|---:|---|---|'])
                for row in rows:
                    activity = activity_rows[row['canonical_smiles']]
                    value, probability = activity.get('predicted_pIC50'), activity.get('activity_probability')
                    value = f'{value:.4g}' if type(value) in (int, float) and math.isfinite(value) else '未返回'
                    probability = (f'{probability:.4g}' if type(probability) in (int, float)
                                   and math.isfinite(probability) else '未返回')
                    consistency = activity.get('classification_regression_consistent')
                    label = '一致' if consistency is True else '不一致，需复核' if consistency is False else '未确定'
                    scope = (activity.get('provenance') or {}).get('scope') or {}
                    species = {'mixed': '混合物种', 'human': '人源'}.get(scope.get('species'), '未标明')
                    lines.append(f"| `{row['canonical_smiles']}` | {value} | {probability} | {label} | {species} |")
                lines.append('活性与 ADMET 均为模型预测；分类与回归不一致的结果需复核，'
                             '混合物种模型不能视为人源特异预测。')
            else:
                lines.append('本次未复核上一轮活性预测，不能据此排序。')
            lines.append('需结合模型适用域、项目筛选标准及靶点活性、选择性和安全性实验；'
                         '预测值不是实验结论，未重新运行工具。')
            self.entry['candidate_evidence'] = deepcopy(evidence)
            return {'content': '\n'.join(lines), 'status': 'partial'}
        endpoint_explanation = re.fullmatch(
            r'(?:刚才|上一轮|上次)(?:的)?水?溶解度(?:\s*(?P<sol>[+-]?(?:\d+(?:\.\d+)?|\.\d+))'
            r'\s*log\s*\(mol/L\))?\s*(?:和|与)\s*肝毒性'
            r'(?:\s*(?P<dili>[+-]?(?:\d+(?:\.\d+)?|\.\d+)))?\s*分别代表什么[？?]\s*'
            r'(?:能否将它们|这些能)当作实验值或人体发生肝损伤的概率(?:吗)?[？?。]?\s*', self.query.strip(), re.I)
        if endpoint_explanation:
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            if (not self.session_id or not isinstance(evidence, dict) or evidence.get('session_id') != self.session_id
                    or not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
                    or not any(source.get('tool_name') == 'admet_predictor' and source.get('evidence_id')
                               for source in rows[0].get('sources', []) if isinstance(source, dict))):
                return {'content': '无法核对上一轮唯一分子的 ADMET 预测记录；未重新预测。', 'status': 'not_calculated'}
            row = rows[0]
            endpoints = row.get('admet', {}).get('endpoints', {})
            lines = [f"上一轮 `{row['canonical_smiles']}` 的模型结果："]
            incomplete = False
            for name, group, task, unit in (
                    ('Solubility_AqSolDB', 'sol', 'regression', 'log(mol/L)'),
                    ('DILI', 'dili', 'classification', '-')):
                endpoint = endpoints.get(name)
                if (not isinstance(endpoint, dict) or endpoint.get('task_type') != task
                        or endpoint.get('unit') != unit or type(endpoint.get('value')) not in (int, float)
                        or not math.isfinite(endpoint['value'])):
                    lines.append(f'{name} 未返回可核对的预测值。')
                    incomplete = True
                    continue
                display = f"{endpoint['value']:.4g}"
                if endpoint_explanation.group(group) is not None and float(endpoint_explanation.group(group)) != float(display):
                    lines.append(f'{name} 的引用数值与上一轮显示值 {display} 不一致，请核对。')
                    incomplete = True
                elif name == 'Solubility_AqSolDB':
                    lines.append(f'{name} 为 {display} log(mol/L)，是水溶解度的对数尺度回归预测，不能当作实验测量值。')
                else:
                    lines.append(f'DILI 为 {display}，是药物性肝损伤端点的模型分类分数，不能直接当作人体发生肝损伤的概率或实验结论。')
            lines.append('预测受模型误差和适用域影响，应结合合适的实验与暴露证据验证；本轮未重新预测。')
            self.entry['candidate_evidence'] = deepcopy(evidence)
            return {'content': ' '.join(lines), 'status': 'partial' if incomplete else 'completed'}
        endpoint_question = re.fullmatch(
            r'(?:上一轮|刚才|上次)(?:的)?(?:ADMET\s*)?结果中\s*'
            r'(?P<names>(?:水?溶解度|肝毒性|DILI|hERG)'
            r'(?:\s*(?:、|，|,|和|与|及)\s*(?:水?溶解度|肝毒性|DILI|hERG))*)'
            r'\s*分别是多少[？?](?:\s*哪些指标缺失[？?])?'
            r'(?:\s*(?:不要|无需|不用)重新预测[。.!！]?)?\s*', self.query, re.I)
        if endpoint_question:
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            if (not self.session_id or not isinstance(evidence, dict) or evidence.get('session_id') != self.session_id
                    or not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
                    or not isinstance(rows[0].get('canonical_smiles'), str)
                    or not any(isinstance(source, dict) and source.get('tool_name') == 'admet_predictor'
                               and source.get('evidence_id') for source in rows[0].get('sources', []))):
                return {'content': '无法确认上一轮唯一分子的 ADMET 记录；未重新预测。', 'status': 'not_calculated'}
            row = rows[0]
            endpoints = row.get('admet', {}).get('endpoints', {})
            aliases = {'溶解度': 'Solubility_AqSolDB', '水溶解度': 'Solubility_AqSolDB',
                       '肝毒性': 'DILI', 'dili': 'DILI', 'herg': 'hERG'}
            requested = list(dict.fromkeys(aliases[token.casefold()] for token in
                re.findall(r'水?溶解度|肝毒性|DILI|hERG', endpoint_question.group('names'), re.I)))
            lines = [f"上一轮 `{row['canonical_smiles']}` 的模型预测："]
            missing = []
            for name in requested:
                detail = endpoints.get(name)
                if (not isinstance(detail, dict) or type(detail.get('value')) not in (int, float)
                        or not math.isfinite(detail['value']) or not isinstance(detail.get('unit'), str)
                        or detail.get('task_type') not in {'regression', 'classification'}):
                    missing.append(name)
                    continue
                kind = '回归' if detail['task_type'] == 'regression' else '分类'
                lines.append(f"{name}：{detail['value']:.4g} {detail['unit']}（{kind}）。")
            if missing:
                lines.append('未返回：' + '、'.join(missing) + '。')
            else:
                lines.append('所请求的端点均已返回。')
            lines.append('这些是模型预测，不能直接作为实验结果或人体安全性结论。未重新预测。')
            self.entry['candidate_evidence'] = deepcopy(evidence)
            return {'content': ' '.join(lines), 'status': 'partial' if missing else 'completed'}
        optimization_followup = re.fullmatch(
            r'(?:基于|根据)(?:刚才|上一轮)的结果[，,]\s*(?:这个|该)分子'
            r'(?:在)?(?:后续)?优化中(?:应|应该)?优先关注哪些性质[？?]'
            r'\s*(?:请)?说明依据和还需要哪些实验数据[。.!！?？]?', self.query.strip())
        if optimization_followup:
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            if (not isinstance(evidence, dict) or evidence.get('session_id') != self.session_id
                    or not isinstance(rows, list) or len(rows) != 1
                    or not isinstance(rows[0], dict)
                    or not isinstance(rows[0].get('canonical_smiles'), str)
                    or not isinstance(rows[0].get('properties'), dict)
                    or any(type(rows[0]['properties'].get(key)) not in (int, float)
                           or not math.isfinite(rows[0]['properties'][key])
                           for key in ('molecular_weight', 'logp'))
                    or not any(source.get('tool_name') == 'property_calculator' and source.get('evidence_id')
                               for source in rows[0].get('sources', []))):
                return {'content': '无法确认上一轮唯一分子的已验证性质，请重新提供结构并计算。',
                        'status': 'not_calculated'}
            row, props = rows[0], rows[0]['properties']
            self.entry['candidate_evidence'] = deepcopy(evidence)
            return {'content': (f"上一轮 `{row['canonical_smiles']}` 的计算分子量为 "
                    f"{props['molecular_weight']:.2f} Da、logP 为 {props['logp']:.3f}。"
                    '它们是结构描述符，不能单独决定优化优先级。'
                    '请明确靶点、作用机制和项目目标；再结合靶点活性与选择性、溶解度、'
                    '渗透性、代谢稳定性和安全性的实验数据，按项目需求权衡。'),
                    'status': 'partial'}
        filter_pattern = (r'只保留满足(?:我)?(?:刚才|上一轮|之前)(?:的)?分子量(?:和|与|及)\s*log\s*p\s*'
                          r'(?:条件|要求|约束)的候选[，,]\s*(?:并)?列出筛选结果[。.!！?？]?')
        filter_request = re.fullmatch(filter_pattern, self.query.strip(), re.I)
        explicit_filter = re.fullmatch(
            r'这两个候选(?:分子)?(?:里|中)[，,]\s*哪些分子量\s*'
            r'(?:小于|低于|不超过|不大于|大于|高于|不低于|不小于|<=|>=|<|>|≤|≥)\s*'
            r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:\s*Da)?\s*(?:且|并且|并)\s*log\s*p\s*'
            r'(?:小于|低于|不超过|不大于|大于|高于|不低于|不小于|<=|>=|<|>|≤|≥)\s*'
            r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)[？?]\s*(?:请)?列出各自数值和判断[。.!！?？]?',
            self.query.strip(), re.I)
        filter_followup = re.fullmatch(r'筛选结果中满足条件的是哪(?:一)?个候选[？?。]?', self.query.strip())
        if filter_followup and (not self.history or not re.fullmatch(
                filter_pattern, self.history[-1].get('user', '').strip(), re.I)):
            return {'content': '无法确认上一轮筛选结果，请先按明确条件筛选候选。', 'status': 'not_calculated'}
        if filter_request or filter_followup or explicit_filter:
            from src.agent.property_constraints import requested_property_bounds, check_property_bounds
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            bounds, unknown = requested_property_bounds(
                self.query if explicit_filter else evidence.get('query', '') if isinstance(evidence, dict) else '')
            bounds = [bound for bound in bounds if bound['metric'] in {'molecular_weight', 'logp'}]
            if (not isinstance(evidence, dict) or evidence.get('session_id') != self.session_id
                    or evidence.get('generation_scope_verified') is not True
                    or not isinstance(rows, list) or not rows or (explicit_filter and len(rows) != 2) or unknown
                    or {bound['metric'] for bound in bounds} != {'molecular_weight', 'logp'}
                    or any(not isinstance(row, dict) or not isinstance(row.get('canonical_smiles'), str)
                           or not any(source.get('tool_name') == 'property_calculator' and source.get('evidence_id')
                                      for source in row.get('sources', [])) for row in rows)):
                return {'content': '无法核对上一轮完整候选集合、原始条件及性质证据，请重新生成并计算。',
                        'status': 'not_calculated'}
            lines = [('按本次分子量和 logP 条件筛选（使用上一轮已计算的描述符）：'
                      if explicit_filter else '按原始分子量和 logP 条件筛选（使用已计算的描述符）：')]
            incomplete = False
            matched = 0
            for row in rows:
                checks = check_property_bounds(bounds, row.get('properties'), rounded=True)
                states = [check['satisfied'] for check in checks]
                status = '不满足' if False in states else '未验证' if None in states else '满足'
                incomplete |= status == '未验证'
                matched += status == '满足'
                properties = row.get('properties', {})
                lines.append(f"`{row['canonical_smiles']}`：分子量 {properties.get('molecular_weight', '未返回')} Da，"
                             f"logP {properties.get('logp', '未返回')}；{status}。")
            if explicit_filter and not matched and not incomplete:
                lines.append('没有候选同时满足这两个条件。')
            self.entry['candidate_evidence'] = deepcopy(evidence)
            return {'content': '\n'.join(lines), 'status': 'partial' if incomplete else 'completed'}
        selectivity_question = re.fullmatch(
            r'(?:请)?预测\s*\S+\s*对\s*(?:BuChE\s*(?:和|与)\s*AChE|AChE\s*(?:和|与)\s*BuChE)'
            r'\s*的(?:抑制)?活性\s*[，,]\s*(?:并)?比较(?:两者|其)的选择性[。.!！?？]?',
            self.query.strip(), re.I)
        if enable_tools and selectivity_question:
            return {'content': ('当前活性预测未接入 AChE，无法与 BuChE 的结果比较选择性；'
                    '本次未执行活性预测。可分别获得相同实验条件下两个靶点的活性数据后比较，'
                    '并明确使用的活性指标和物种。'), 'status': 'rejected'}
        supplied_pic50 = re.fullmatch(
            r'已知(?:某)?(?:实验测得)?\s*(?i:pIC50)\s*'
            r'(?P<relation>大于等于|小于等于|大于|小于|>=|<=|≥|≤|>|<|为|是|=|：|:)\s*'
            r'(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*[，,]\s*(?:请)?'
            r'(?:计算|换算)(?:对应的)?\s*(?i:IC50)\s*[（(]\s*(?P<unit>M|mM|uM|µM|μM|nM|pM)\s*[）)]'
            r'\s*(?:并给出换算公式)?[。.!！?？]?\s*', self.query.strip())
        if supplied_pic50:
            number = float(supplied_pic50.group('value'))
            if not math.isfinite(number):
                return {'content': 'pIC50 必须是有限数值，请检查后重新提供。', 'status': 'rejected'}
            unit = supplied_pic50.group('unit')
            scale = {'M': 0, 'mM': 3, 'uM': 6, 'µM': 6, 'μM': 6, 'nM': 9, 'pM': 12}[unit]
            exponent = scale - number
            formula = f"10^({scale} − {supplied_pic50.group('value')}) {unit}"
            if not -323 <= exponent <= 308:
                return {'content': f'您提供的 pIC50 对应 IC50 的换算式为 {formula}；结果超出当前数值表示范围。',
                        'status': 'partial'}
            concentration = 10 ** exponent
            relation = supplied_pic50.group('relation')
            inverse = {'大于': '<', '>': '<', '小于': '>', '<': '>',
                       '大于等于': '≤', '>=': '≤', '≥': '≤',
                       '小于等于': '≥', '<=': '≥', '≤': '≥'}
            if relation in inverse:
                return {'content': (f"您提供的 pIC50 {relation} {supplied_pic50.group('value')}；"
                        f"IC50 {inverse[relation]} {formula}，换算限值约为 {concentration:.4g} {unit}，"
                        '不能作为精确测量值。'), 'status': 'completed'}
            return {'content': (f"您提供的 pIC50 为 {supplied_pic50.group('value')}；"
                    f'按 IC50 = {formula}，换算约为 {concentration:.4g} {unit}。'), 'status': 'completed'}
        supplied_ic50 = re.fullmatch(
            r'已知(?:某)?(?:实验测得)?\s*(?i:IC50)\s*'
            r'(?P<relation>大于等于|小于等于|大于|小于|>=|<=|≥|≤|>|<|为|是|=|：|:)\s*'
            r'(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*'
            r'(?P<unit>M|mM|uM|µM|μM|nM|pM)\s*[，,]\s*(?:请)?'
            r'(?:(?:计算|换算)(?:对应的)?\s*(?i:pIC50)\s*(?:并给出换算公式)?[。.!！?？]?'
            r'|对应的\s*(?i:pIC50)\s*(?:应该)?是多少[？?。]'
            r'\s*(?:请说明不等号方向[。.!！?？]?)?)\s*',
            self.query.strip())
        if supplied_ic50:
            number = float(supplied_ic50.group('value'))
            if not math.isfinite(number) or number <= 0:
                return {'content': 'IC50 必须是大于零的有限数值，请检查后重新提供。', 'status': 'rejected'}
            exponent = {'M': 0, 'mM': -3, 'uM': -6, 'µM': -6, 'μM': -6, 'nM': -9, 'pM': -12}[
                supplied_ic50.group('unit')]
            pic50 = -math.log10(number) - exponent
            relation = supplied_ic50.group('relation')
            inverse = {'大于': '<', '>': '<', '小于': '>', '<': '>',
                       '大于等于': '≤', '>=': '≤', '≥': '≤',
                       '小于等于': '≥', '<=': '≥', '≤': '≥'}
            if relation in inverse:
                return {'content': (f"您提供的 IC50 {relation} {supplied_ic50.group('value')} {supplied_ic50.group('unit')}。"
                        f"按 pIC50 = −log₁₀(IC50 / mol·L⁻¹)，pIC50 {inverse[relation]} "
                        f"−log₁₀({supplied_ic50.group('value')} × 10^{exponent})；"
                        f"换算限值约为 {pic50:.4f}，不能作为精确测量值。"), 'status': 'completed'}
            return {'content': (f"您提供的 IC50 为 {supplied_ic50.group('value')} {supplied_ic50.group('unit')}。"
                    f"按 pIC50 = −log₁₀(IC50 / mol·L⁻¹) 换算，pIC50 = {pic50:.4f}。"),
                    'status': 'completed'}
        logd_question = re.fullmatch(
            r'(?:刚才|上一轮)(?:的)?\s*log\s*p\s*(?:能否|能不能|能)直接当作'
            r'(?:这个|该)分子在\s*pH\s*\d{1,2}(?:\.\d{1,2})?\s*下的\s*log\s*d\s*吗[？?]'
            r'\s*(?:为什么[？?。]?)?', self.query.strip(), re.I)
        batch_logd_question = re.fullmatch(
            r'(?:这些|上述|刚才的)结果(?:能否|能不能|能)直接(?:推断|确定)\s*'
            r'pH\s*\d{1,2}(?:\.\d{1,2})?\s*(?:时|下)的\s*log\s*d\s*吗[？?。]?',
            self.query.strip(), re.I)
        batch_logd_value = re.fullmatch(
            r'(?:那|那么)?\s*在\s*pH\s*\d{1,2}(?:\.\d{1,2})?\s*下'
            r'(?:它们|这两个分子|这些分子)的\s*log\s*d\s*(?:呢|是多少)[？?。]?',
            self.query.strip(), re.I)
        if enable_tools and (batch_logd_question or batch_logd_value):
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            if (isinstance(evidence, dict) and evidence.get('session_id') == self.session_id
                    and isinstance(rows, list) and rows
                    and all(isinstance(row, dict) and isinstance(row.get('canonical_smiles'), str)
                            and type(row.get('properties', {}).get('logp')) in (int, float)
                            and math.isfinite(row['properties']['logp'])
                            and any(source.get('tool_name') == 'property_calculator' and source.get('evidence_id')
                                    for source in row.get('sources', [])) for row in rows)):
                self.entry['candidate_evidence'] = deepcopy(evidence)
                if batch_logd_value:
                    molecules = '、'.join(f"`{row['canonical_smiles']}`" for row in rows)
                    return {'content': (f'上一轮的分子为 {molecules}。本次未计算指定 pH 下的 logD；'
                            'logP 不能直接代替 logD，需要相关电离状态及各形式的分配信息。'),
                            'status': 'not_calculated'}
                return {'content': ('不能直接推断。logP 描述中性形式的分配；logD 还受指定 pH 下'
                        '离子化状态及各形式分配的影响。本次未计算该 pH 下的 logD，'
                        '仅凭这些 logP 结果不能确定其数值。'), 'status': 'completed'}
            return {'content': '无法确认上一轮的 logP 记录；请提供结构并重新计算。',
                    'status': 'not_calculated'}
        if enable_tools and logd_question:
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            if (isinstance(evidence, dict) and evidence.get('session_id') == self.session_id
                    and isinstance(rows, list) and len(rows) == 1
                    and isinstance(rows[0].get('canonical_smiles'), str)
                    and type(rows[0].get('properties', {}).get('logp')) in (int, float)
                    and math.isfinite(rows[0]['properties']['logp'])
                    and any(source.get('tool_name') == 'property_calculator' and source.get('evidence_id')
                            for source in rows[0].get('sources', []))):
                row = rows[0]
                self.entry['candidate_evidence'] = deepcopy(evidence)
                return {'content': (f"不能直接等同。上一轮 `{row['canonical_smiles']}` 的计算 logP 为 "
                        f"{row['properties']['logp']:.3f}。logP 描述中性形式在两相间的分配；"
                        'logD 还受指定 pH 下离子化状态及各形式分配的影响。'
                        '本次未计算该 pH 下的 logD，不能从这个 logP 单独确定其数值。'),
                        'status': 'completed'}
            return {'content': '无法确认上一轮单个分子的 logP 记录；请提供结构并重新计算。',
                    'status': 'not_calculated'}
        property_question = property_comparison.deictic_property_match(self.query)
        if enable_tools and self.history and property_question:
            evidence = self.history[-1].get('candidate_evidence')
            if isinstance(evidence, dict) and evidence.get('session_id') == self.session_id:
                rows = evidence.get('candidates')
                if (isinstance(rows, list) and len(rows) > 1
                        and all(isinstance(row, dict) and any(
                            source.get('tool_name') == 'property_calculator' and source.get('evidence_id')
                            for source in row.get('sources', [])) for row in rows)):
                    self.entry['property_clarification'] = {
                        'session_id': self.session_id, 'metrics': property_question.group(1),
                        'candidates': [row['canonical_smiles'] for row in rows]}
                    return {'content': f'上一轮包含多个分子，请提供要查询 {property_question.group(1)} 的分子 SMILES。',
                            'status': 'rejected'}
        choice = re.fullmatch(
            r'根据(?:刚才|上一轮)的性质[，,]\s*哪(?:一)?个(?:分子|候选)更适合继续优化[？?]'
            r'\s*还需要哪些证据[？?。]?', self.query.strip())
        if choice:
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            if (not isinstance(evidence, dict) or evidence.get('session_id') != self.session_id
                    or not isinstance(rows, list) or len(rows) != 2
                    or any(not isinstance(row, dict) or not isinstance(row.get('canonical_smiles'), str)
                           or not isinstance(row.get('properties'), dict)
                           or any(type(row['properties'].get(key)) not in (int, float)
                                  or not math.isfinite(row['properties'][key]) for key in ('molecular_weight', 'logp'))
                           or not any(source.get('tool_name') == 'property_calculator' and source.get('evidence_id')
                                      for source in row.get('sources', [])) for row in rows)):
                return {'content': '无法确认上一轮两个分子的已验证性质，请重新提供结构并计算。', 'status': 'not_calculated'}
            lines = ['仅凭这些计算性质不能确定哪个分子更适合优化；需要先明确靶点和优化目标。']
            for row in rows:
                props = row['properties']
                text = f"`{row['canonical_smiles']}`：分子量 {props['molecular_weight']:.2f} Da，logP {props['logp']:.3f}"
                if type(props.get('tpsa')) in (int, float) and math.isfinite(props['tpsa']):
                    text += f"，TPSA {props['tpsa']:.2f} Å²"
                lines.append(text + '。')
            lines.append('还需结合相同条件下的靶点活性与选择性实验、溶解度和渗透性、代谢稳定性、安全性以及合成可行性，再按项目目标权衡。')
            self.entry['candidate_evidence'] = deepcopy(evidence)
            return {'content': '\n\n'.join(lines), 'status': 'partial'}
        relist = re.fullmatch(
            r'(?:请)?(?:列出|展示|显示)(?:刚才|上一轮|上次)(?:的)?排名前\s*'
            r'(?P<count>[1-9]|10|[一二三四五六七八九十])\s*(?:个)?(?:候选)?靶点'
            r'(?:及其支持证据)?[。.!！?？]?', self.query.strip())
        if relist:
            pointer = self.history[-1].get('reverse_target_evidence') if self.history else None
            value = relist.group('count')
            count = int(value) if value.isdigit() else '一二三四五六七八九十'.index(value) + 1
            content = (self.reverse_boundary.render_prefix(pointer, session_id=self.session_id, count=count, concise=True)
                       if self.reverse_boundary is not None and pointer is not None else None)
            if content is None:
                return {'content': '无法核对上一轮排名证据，请重新运行反向寻靶。', 'status': 'partial'}
            self.entry['reverse_target_evidence'] = deepcopy(pointer)
            return {'content': content, 'status': 'completed'}
        from src.agent.target_structure_followup import structure_followup
        structure_view = structure_followup(self.query, self.history, self.session_id, enable_tools=enable_tools)
        if structure_view is not None:
            if structure_view.get('evidence') is not None:
                self.entry['target_structure_evidence'] = structure_view['evidence']
            if structure_view.get('function_evidence') is not None:
                self.entry['target_function_evidence'] = structure_view['function_evidence']
            return {'content': structure_view['content'], 'status': structure_view['status']}
        absorption_comparison = re.fullmatch(
            r'这是否意味着它一定比另一个分子更容易口服吸收[？?。]?', self.query.strip())
        if absorption_comparison:
            previous = self.history[-1] if self.history else {}
            prior_query = previous.get('user', '')
            prior = re.fullmatch(_PROPERTY_FOLLOWUP + _PROPERTY_FOLLOWUP_END, prior_query, re.I)
            prior_candidate = re.fullmatch(_CANDIDATE_LIPOPHILICITY, prior_query, re.I)
            evidence = previous.get('candidate_evidence')
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            if ((not prior_candidate and (not prior or prior.group('metric').replace(' ', '').casefold() not in ('logp', '亲脂性')
                                         or prior.group('direction') not in ('高', '大')))
                    or not isinstance(evidence, dict) or evidence.get('session_id') != self.session_id
                    or not isinstance(rows, list) or len(rows) != 2
                    or any(not isinstance(row, dict) or not isinstance(row.get('canonical_smiles'), str)
                           or not isinstance(row.get('properties'), dict)
                           or type(row['properties'].get('logp')) not in (int, float)
                           or not math.isfinite(row['properties']['logp'])
                           or not any(source.get('tool_name') == 'property_calculator' and source.get('evidence_id')
                                      for source in row.get('sources', [])) for row in rows)
                    or rows[0]['properties']['logp'] == rows[1]['properties']['logp']):
                return {'content': '无法确认上一轮比较所指的分子和有效 LogP 记录，请先重新比较。',
                        'status': 'not_calculated'}
            chosen = max(rows, key=lambda row: row['properties']['logp'])
            self.entry['candidate_evidence'] = deepcopy(evidence)
            return {'content': (f"不能。上一轮 `{chosen['canonical_smiles']}` 的计算 LogP 较高，"
                    '但仅凭该描述符不能确定它比另一个分子更容易口服吸收。'
                    '还需结合溶解度、溶出、离子化状态、渗透性及转运等证据；本轮未测量这些性质。'),
                    'status': 'completed'}
        bbb_explanation = re.fullmatch(
            r'(?:刚才|上一轮|上次)(?:的)?(?:血脑屏障|BBB)(?:通透性)?'
            r'(?:预测分数|预测值|分类分数)(?:具体)?(?:是|代表|意味着)什么(?:意思)?[？?]'
            r'\s*(?:(?:能否|能不能|请)?说明(?:该|这个)?(?:结果|预测)(?:的)?(?:局限|限制|不确定性)[？?。]?)?',
            self.query.strip(), re.I)
        if bbb_explanation:
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            if isinstance(evidence, dict) and evidence.get('session_id') == self.session_id and isinstance(rows, list) and len(rows) == 1:
                row = rows[0]
                endpoint = row.get('admet', {}).get('endpoints', {}).get('BBB_Martins')
                if (isinstance(row.get('canonical_smiles'), str) and isinstance(endpoint, dict)
                        and endpoint.get('task_type') == 'classification'
                        and type(endpoint.get('value')) in (int, float)
                        and math.isfinite(endpoint['value']) and 0 <= endpoint['value'] <= 1
                        and any(source.get('tool_name') == 'admet_predictor' and source.get('evidence_id')
                                for source in row.get('sources', []))):
                    self.entry['candidate_evidence'] = deepcopy(evidence)
                    return {'content': (f"上一轮 `{row['canonical_smiles']}` 的血脑屏障模型分类分数为 {endpoint['value']:.4g}。"
                            '这是模型预测，不是实测通透率，也不能直接作为人体脑暴露或临床效果的概率。'
                            '本次未评估该结构是否处于模型适用域；结果受训练数据和模型误差影响。'
                            '可结合适当的血脑屏障相关体外实验、转运体信息及必要的体内药代实验验证。'),
                            'status': 'completed'}
            return {'content': '上一轮没有可核对的单一分子血脑屏障分类预测记录，请提供结构并重新预测。',
                    'status': 'not_calculated'}
        bioavailability_question = re.fullmatch(
            r'Bioavailability_Ma\s*的预测值(?:具体)?是什么意思[？?]\s*'
            r'能(?:否|不能|直接)?(?:直接)?当作口服生物利用度百分比吗[？?。]?',
            self.query.strip(), re.I)
        if bioavailability_question:
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            if isinstance(evidence, dict) and evidence.get('session_id') == self.session_id and isinstance(rows, list) and len(rows) == 1:
                row = rows[0]
                endpoint = row.get('admet', {}).get('endpoints', {}).get('Bioavailability_Ma')
                if (isinstance(endpoint, dict) and endpoint.get('task_type') == 'classification'
                        and any(source.get('tool_name') == 'admet_predictor' and source.get('evidence_id')
                                for source in row.get('sources', []))):
                    self.entry['candidate_evidence'] = deepcopy(evidence)
                    return {'content': (f"上一轮 Bioavailability_Ma 的模型分类输出为 {endpoint['value']:.4g}。"
                            '这是分类预测分数，不能直接当作口服生物利用度百分比或实验测量值；'
                            '具体分类标签含义应结合该端点的数据定义，模型输出也不能替代药代实验。'),
                            'status': 'completed'}
            return {'content': '上一轮没有可核对的 Bioavailability_Ma 分类预测记录，无法解释具体数值。',
                    'status': 'not_calculated'}
        oral_followup = re.fullmatch(
            r'(?:(?:根据|基于)(?:刚才|上一轮)(?:的)?结果[，,]\s*'
            r'(?:(?:能否|可以否)断定(?:这个|该)分子口服吸收良好|'
            r'(?:这个|该)分子(?:的)?口服生物利用度(?:一定|肯定)(?:很)?高吗)[？?]\s*'
            r'(?:还)?需要哪些(?:实验(?:来)?验证|数据(?:才能)?判断)[？?。]?|'
            r'这些数值能保证(?:这个|该)分子口服吸收好吗[？?]\s*为什么[？?。]?)', self.query.strip())
        if oral_followup:
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            if (not isinstance(evidence, dict) or evidence.get('session_id') != self.session_id
                    or not isinstance(rows, list) or len(rows) != 1
                    or not isinstance(rows[0].get('canonical_smiles'), str)
                    or not isinstance(rows[0].get('properties'), dict)
                    or not any(source.get('tool_name') == 'property_calculator' and source.get('evidence_id')
                               for source in rows[0].get('sources', []))):
                return {'content': '无法确认上一轮单个分子的性质计算记录，请提供结构并重新计算。',
                        'status': 'not_calculated'}
            self.entry['candidate_evidence'] = deepcopy(evidence)
            props = rows[0]['properties']
            reported = []
            if type(props.get('logp')) in (int, float) and math.isfinite(props['logp']):
                reported.append(f"logP {props['logp']:.3f}")
            if type(props.get('tpsa')) in (int, float) and math.isfinite(props['tpsa']):
                reported.append(f"TPSA {props['tpsa']:.2f} Å²")
            values = '（' + '，'.join(reported) + '）' if reported else ''
            return {'content': (f"不能仅凭上一轮 `{rows[0]['canonical_smiles']}` 的计算描述符{values}断定口服吸收良好或生物利用度高。"
                    '可先测定相关 pH 下的溶解度和溶出，再结合 PAMPA 或 Caco-2 渗透性及必要的转运体实验；'
                    '进一步可通过口服与静脉药代研究评估生物利用度；区分吸收与首过损失可能需要额外研究。'
                    '具体实验应结合化合物稳定性、制剂和研究目标设计；这些实验本轮均未执行。'),
                    'status': 'completed'}
        property_values = re.fullmatch(
            r'(?:请问)?(?:刚才|上一轮|上次)(?:的)?(?:该|这个)分子(?:的)?\s*'
            r'(?P<metrics>(?:LogP|TPSA|分子量|MW)(?:\s*(?:和|与|及|、|[,，])\s*'
            r'(?:LogP|TPSA|分子量|MW))*)\s*(?:具体)?(?:分别)?(?:是)?多少[？?]'
            r'(?:\s*请引用实际计算值[。.!！]?)?', self.query.strip(), re.I)
        if property_values:
            metric_info = {'logp': ('logp', 'logP', '.3f', ''),
                           'tpsa': ('tpsa', 'TPSA', '.2f', ' Å²'),
                           'mw': ('molecular_weight', '分子量', '.2f', ' Da'),
                           '分子量': ('molecular_weight', '分子量', '.2f', ' Da')}
            metrics = list(dict.fromkeys(metric_info[name.casefold()] for name in
                re.findall(r'LogP|TPSA|分子量|MW', property_values.group('metrics'), re.I)))
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            if (not isinstance(evidence, dict) or evidence.get('session_id') != self.session_id
                    or not isinstance(rows, list) or len(rows) != 1
                    or not isinstance(rows[0].get('canonical_smiles'), str)
                    or not isinstance(rows[0].get('properties'), dict)
                    or not all(type(rows[0]['properties'].get(key)) in (int, float)
                               and math.isfinite(rows[0]['properties'][key]) for key, _, _, _ in metrics)
                    or not any(source.get('tool_name') == 'property_calculator' and source.get('evidence_id')
                               for source in rows[0].get('sources', []))):
                return {'content': '无法确认当前会话中单一分子的所需计算记录，请提供结构并重新计算。',
                        'status': 'not_calculated'}
            self.entry['candidate_evidence'] = deepcopy(evidence)
            props = rows[0]['properties']
            values = '，'.join(f'{label} {format(props[key], precision)}{unit}'
                               for key, label, precision, unit in metrics)
            return {'content': f"`{rows[0]['canonical_smiles']}` 的计算值：{values}。", 'status': 'completed'}
        comparison = re.fullmatch(_PROPERTY_FOLLOWUP + _PROPERTY_FOLLOWUP_END,
                                  self.mixed_history_query or self.query.strip(), re.I)
        candidate_question = self.mixed_history_query or self.query.strip()
        candidate_oral = re.fullmatch(
            r'(?P<comparison>' + _CANDIDATE_LIPOPHILICITY + r')\s*'
            r'这(?:能|能够|是否能|能否)证明它(?:的)?口服吸收更好吗[？?。]?',
            candidate_question, re.I)
        candidate_lipophilicity = re.fullmatch(
            _CANDIDATE_LIPOPHILICITY,
            candidate_oral.group('comparison') if candidate_oral else candidate_question, re.I)
        if comparison or candidate_lipophilicity:
            is_logp = bool(candidate_lipophilicity) or re.sub(r'\s+', '', comparison.group('metric')).casefold() in ('logp', '亲脂性')
            key, label, unit = ('logp', 'LogP', '') if is_logp else ('molecular_weight', '分子量', ' Da')
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            if (not isinstance(evidence, dict) or evidence.get('session_id') != self.session_id
                    or not isinstance(rows, list) or len(rows) != 2
                    or any(not isinstance(row, dict) or not isinstance(row.get('canonical_smiles'), str)
                           or not isinstance(row.get('properties'), dict)
                           or type(row['properties'].get(key)) not in (int, float)
                           or not math.isfinite(row['properties'][key])
                           or not any(source.get('tool_name') == 'property_calculator'
                                      and source.get('evidence_id') for source in row.get('sources', []))
                           for row in rows)):
                answer = {'content': f'无法确认上一轮两个分子的已验证{label}，请重新提供结构并计算。',
                          'status': 'not_calculated'}
                if self.mixed_history_query is not None:
                    self.mixed_history_view = answer
                    return None
                return answer
            values = [row['properties'][key] for row in rows]
            precision = 3 if is_logp else 2
            direction = '高' if candidate_lipophilicity else comparison.group('direction')
            higher = direction in ('高', '大')
            chosen = 0 if (values[0] > values[1]) == higher else 1
            conclusion = (f'两者显示的计算{label}相同；舍入精度不足以判断差异。' if values[0] == values[1] else
                          f"`{rows[chosen]['canonical_smiles']}` 的计算{label}更{direction}。")
            self.entry['candidate_evidence'] = deepcopy(evidence)
            answer = {'content': (f"`{rows[0]['canonical_smiles']}`：{values[0]:.{precision}f}{unit}；"
                                  f"`{rows[1]['canonical_smiles']}`：{values[1]:.{precision}f}{unit}。{conclusion}"),
                      'status': 'partial' if values[0] == values[1] else 'completed'}
            if candidate_oral:
                answer['content'] += ('不能仅凭计算 LogP 证明哪一个的口服吸收更好；'
                                      '还需结合溶解度、溶出、离子化状态、渗透性及转运等证据。')
            if self.mixed_history_query is not None:
                self.mixed_history_view = answer
                return None
            return answer
        herg_comparison = re.fullmatch(
            r'(?:根据|基于)(?:刚才|上一轮|上次)(?:的)?结果[，,]\s*'
            r'(?:这两个|两个|两种)(?:分子|候选)(?:的)?\s*hERG\s*(?:预测|预测值)'
            r'(?:有何差异|有什么差异|有什么不同|如何比较)[？?]'
            r'\s*(?:这些|这两个)(?:数值|预测值)(?:能否|能不能|是否能)'
            r'(?:说明|判断)(?:哪一个|哪个)(?:在人体中|对人体)(?:更安全)[？?。]?',
            self.query.strip(), re.I)
        herg_order = re.fullmatch(
            r'(?:刚才|上一轮|上次)?(?:这)?两个(?:分子|候选)(?:中)?'
            r'(?:哪一个|哪个)(?:的)?\s*hERG\s*(?:的)?预测值(?:更|较)(?P<direction>高|低)[？?。]?',
            self.query.strip(), re.I)
        if herg_comparison or herg_order:
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            if (not isinstance(evidence, dict) or evidence.get('session_id') != self.session_id
                    or not isinstance(rows, list) or len(rows) != 2
                    or any(not isinstance(row.get('canonical_smiles'), str)
                           or not any(source.get('tool_name') == 'admet_predictor' and source.get('evidence_id')
                                      for source in row.get('sources', []))
                           or not isinstance(row.get('admet', {}).get('endpoints', {}).get('hERG'), dict)
                           or row['admet']['endpoints']['hERG'].get('task_type') != 'classification'
                           for row in rows)):
                return {'content': '无法确认上一轮两个分子的 hERG 分类预测记录，请重新提供结构并预测。',
                        'status': 'not_calculated'}
            details = [row['admet']['endpoints']['hERG'] for row in rows]
            if details[0]['unit'] != details[1]['unit']:
                return {'content': '上一轮两个 hERG 端点的单位不一致，无法直接比较；不能据此判断人体安全性。',
                        'status': 'partial'}
            values = [float(f"{detail['value']:.4g}") for detail in details]
            comparison = ('显示精度下两者相同。' if values[0] == values[1] else
                f"`{rows[0 if values[0] > values[1] else 1]['canonical_smiles']}` 的模型分类预测值较高。")
            self.entry['candidate_evidence'] = deepcopy(evidence)
            if herg_order and herg_order.group('direction') == '低' and values[0] != values[1]:
                comparison += f"`{rows[0 if values[0] < values[1] else 1]['canonical_smiles']}` 的预测值较低。"
            if herg_order and values[0] != values[1]:
                direction = herg_order.group('direction')
                index = 0 if (values[0] > values[1]) == (direction == '高') else 1
                self.entry['herg_compared'] = {'smiles': rows[index]['canonical_smiles'], 'direction': direction}
            return {'content': (f"上一轮 hERG 分类预测：`{rows[0]['canonical_smiles']}` 为 {values[0]:.4g}；"
                    f"`{rows[1]['canonical_smiles']}` 为 {values[1]:.4g}。{comparison}"
                    '这些是模型预测，不能据此判断哪个分子在人体中更安全；'
                    '还需结合模型适用域、hERG 实验、暴露水平和其他安全性证据。'),
                    'status': 'completed'}
        herg_safety_followup = re.fullmatch(
            r'(?:那|这)(?:是不是|是否|能否)(?:说明|意味着|证明)'
            r'(?:它|该分子)(?:在人体中|对人体)(?:更不安全|更安全)[？?。]?', self.query.strip())
        if herg_safety_followup:
            previous = self.history[-1] if self.history else {}
            evidence = previous.get('candidate_evidence')
            compared = previous.get('herg_compared')
            if (not isinstance(evidence, dict) or evidence.get('session_id') != self.session_id
                    or not isinstance(compared, dict) or compared.get('direction') not in ('高', '低')
                    or compared.get('smiles') not in [row.get('canonical_smiles') for row in evidence.get('candidates', [])]):
                return {'content': '无法确认上一轮所指的 hERG 比较，请重新提供分子并比较。',
                        'status': 'not_calculated'}
            self.entry['candidate_evidence'] = deepcopy(evidence)
            return {'content': (f"不能。上一轮 `{compared['smiles']}` 的 hERG 模型分类预测值较{compared['direction']}，"
                    '但不能仅凭该预测判断它在人体中更安全或更不安全；'
                    '还需结合模型适用域、hERG 实验、暴露水平及其他安全性证据。'),
                    'status': 'completed'}
        herg_safety_question = re.fullmatch(
            r'(?:这个|该|刚才的)\s*hERG\s*预测值(?:是否|能否)(?:说明|意味着|证明)'
            r'(?:该|这个)分子(?:一定|必然)(?:没有|不会有)心脏毒性[？?。]?',
            self.query.strip(), re.I)
        natural_herg_safety = re.fullmatch(
            r'(?:这个|该|刚才的)\s*hERG\s*(?:预测值|分数)(?:很|比较|较)?低[，,]\s*'
            r'(?:是不是|是否|能否)(?:说明|意味着|证明)\s*(?P<smiles>[^\s，,。？?]+)\s*'
            r'对(?:人体)?心脏(?:是)?安全(?:的)?[？?。]?', self.query.strip(), re.I)
        herg_probability = re.fullmatch(
            r'(?:刚才的|上一轮的)?\s*hERG\s*(?:预测值|分数)\s*(?P<score>\d+(?:\.\d+)?)\s*'
            r'(?:是否|是不是|能否)(?:意味着|说明|证明)\s*(?P<smiles>[^\s，,。？?]+)\s*'
            r'有\s*\d+(?:\.\d+)?\s*%\s*的概率(?:出现|发生|导致)(?:人体)?心脏毒性[？?。]?',
            self.query.strip(), re.I)
        previous_evidence = self.history[-1].get('candidate_evidence') if self.history else None
        named_herg_safety = re.fullmatch(
            r'(?:这个|该)(?:数值|预测值|结果)(?:是否|是不是|意味着|能否)(?:说明|意味着|证明)?\s*'
            r'(?P<smiles>\S+)\s*(?:一定|必然)(?:没有|不会有)心脏毒性吗[？?]'
            r'\s*(?:还)?需要(?:什么|哪些)实验(?:来)?验证[？?。]?', self.query.strip(), re.I)
        prior_endpoint = re.fullmatch(
            r'(?:刚才|上一轮|上次)\s*(?P<smiles>\S+)\s*的\s*hERG\s*预测值是多少[？?。]?',
            self.history[-1].get('user', '').strip() if self.history else '', re.I)
        if named_herg_safety and prior_endpoint and enable_tools:
            from src.agent.candidate_evidence_followup import _canonical
            requested = _canonical(named_herg_safety.group('smiles'))
            previous = _canonical(prior_endpoint.group('smiles'))
            rows = previous_evidence.get('candidates', []) if isinstance(previous_evidence, dict) else []
            row = next((item for item in rows if item.get('canonical_smiles') == requested), None)
            if (requested and requested == previous and isinstance(previous_evidence, dict)
                    and previous_evidence.get('session_id') == self.session_id
                    and row and any(source.get('tool_name') == 'admet_predictor' and source.get('evidence_id')
                                    for source in row.get('sources', []))):
                detail = row.get('admet', {}).get('endpoints', {}).get('hERG')
                if detail and 'hit' in detail and 'threshold' in detail:
                    self.entry['candidate_evidence'] = deepcopy(previous_evidence)
                    state = '命中' if detail['hit'] else '未命中'
                    return {'content': (f"不能。`{requested}` 的 hERG 分类预测值为 {detail['value']:.4g}，"
                            f"筛查阈值为 {detail['threshold']:.4g}，{state}阈值；这不能证明没有心脏毒性。"
                            '可用不同浓度的 hERG 电流抑制实验（如膜片钳）验证，结合暴露水平及其他离子通道、'
                            '心肌细胞电生理等实验评估；具体方案应依据项目和风险制定。'), 'status': 'completed'}
            return {'content': '无法确认该分子与上一轮 hERG 预测记录的对应关系，请重新提供结构并预测。',
                    'status': 'not_calculated'}
        if (not herg_safety_question and isinstance(previous_evidence, dict)
                and re.search(r'\bhERG\b', previous_evidence.get('query', ''), re.I)):
            herg_safety_question = re.fullmatch(
                r'(?:这|这个|该)(?:个)?结果(?:是不是|是否|能否)(?:说明|意味着|证明)'
                r'(?:它|该分子|这个分子)对心脏(?:是)?安全(?:的)?[？?。]?', self.query.strip())
        if herg_safety_question or natural_herg_safety or herg_probability:
            evidence = previous_evidence
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            if (not isinstance(evidence, dict) or evidence.get('session_id') != self.session_id
                    or not isinstance(rows, list) or len(rows) != 1
                    or not isinstance(rows[0], dict) or not isinstance(rows[0].get('canonical_smiles'), str)
                    or not any(source.get('tool_name') == 'admet_predictor' and source.get('evidence_id')
                               for source in rows[0].get('sources', []))):
                return {'content': '无法确认上一轮单个分子的 hERG 预测记录，请重新提供结构并预测。',
                        'status': 'not_calculated'}
            if natural_herg_safety or herg_probability:
                from src.agent.candidate_evidence_followup import _canonical
                named_question = natural_herg_safety or herg_probability
                if _canonical(named_question['smiles']) != rows[0]['canonical_smiles']:
                    return {'content': '所问分子与上一轮 hERG 预测记录不一致，请核对结构。',
                            'status': 'not_calculated'}
            detail = rows[0].get('admet', {}).get('endpoints', {}).get('hERG')
            if not isinstance(detail, dict) or 'hit' not in detail or 'threshold' not in detail:
                return {'content': '上一轮缺少可核对的 hERG 分类预测值或筛查阈值，无法判断。',
                        'status': 'partial'}
            if herg_probability:
                if float(herg_probability['score']) != float(f"{detail['value']:.4g}"):
                    self.entry['candidate_evidence'] = deepcopy(evidence)
                    return {'content': '所给预测值与上一轮记录不一致，请核对上一轮结果。',
                            'status': 'not_calculated'}
                self.entry['candidate_evidence'] = deepcopy(evidence)
                return {'content': (f"不能。上一轮 `{rows[0]['canonical_smiles']}` 的 hERG 分类预测值为 {detail['value']:.4g}。"
                        '这是模型分类分数，不能直接换算成人体发生心脏毒性的概率，也不是实测结果；'
                        '应结合模型适用域、hERG 实验、暴露水平和其他安全性证据评估。'),
                        'status': 'completed'}
            self.entry['candidate_evidence'] = deepcopy(evidence)
            state = '命中' if detail['hit'] else '未命中'
            return {'content': (f"不能。上一轮 `{rows[0]['canonical_smiles']}` 的 hERG 分类预测值为 "
                    f"{detail['value']:.4g}，筛查阈值为 {detail['threshold']:.4g}，{state}阈值。"
                    '这是模型预测，不能证明该分子没有心脏毒性；应结合模型适用范围、'
                    'hERG 实验及其他相关安全性证据判断。'), 'status': 'completed'}
        risk_question = re.fullmatch(
            r'(?:请)?(?:刚才|上一轮|上次)(?:的)?(?:ADMET\s*)?(?:预测|结果)'
            r'(?:里|中)?(?:有哪些风险提示|有什么风险提示|风险提示是什么)[？?。]'
            r'(?:\s*(?:这|该结果)能证明(?P<subject>[^，,。？?]{1,32})有肝毒性吗[？?]?)?',
            self.mixed_history_query or self.query.strip(), re.I)
        risk_detail_question = re.fullmatch(
            r'(?:刚才|上一轮|上次)(?:的)?肝损伤风险提示的预测值和判定阈值分别是多少[？?]'
            r'\s*这能证明(?P<subject>[^，,。？?]{1,32}?)会导致肝损伤吗[？?。]?',
            self.mixed_history_query or self.query.strip(), re.I)
        short_dili_question = re.fullmatch(
            r'(?:刚才|上一轮|上次)?\s*(?:的)?\s*(?:DILI|肝损伤)\s*(?:的)?\s*预测值是多少[？?]'
            r'\s*(?:判定|筛查)?阈值是多少[？?。]?',
            self.query.strip(), re.I)
        scored_dili_question = re.fullmatch(
            r'DILI\s*的\s*(?P<score>\d+(?:\.\d+)?)\s*(?:是不是|是否|能否)'
            r'(?:说明|意味着|证明)(?P<subject>[^，,。？?]{1,32}?)'
            r'(?:一定|必然)(?:会)?(?:导致|引起|造成)(?:肝损伤|肝毒性)[？?。]?',
            self.query.strip(), re.I)
        dili_probability_question = re.fullmatch(
            r'(?:所以|那么)?\s*(?P<subject>[^，,。？?\s]{1,32})\s*有\s*'
            r'\d+(?:\.\d+)?\s*[%％]\s*的概率(?:会)?(?:引起|导致|造成)'
            r'(?:人体)?(?:肝损伤|肝毒性)吗[？?。]?', self.query.strip(), re.I)
        from src.agent.contracts.concept_request import _DILI_SCORE_INTERPRETATION
        scored_dili_probability = _DILI_SCORE_INTERPRETATION.fullmatch(self.query.strip())
        dili_validation_question = re.fullmatch(
            r'(?:刚才|上一轮|上次)(?:的)?\s*DILI\s*(?:分数|预测值)'
            r'(?:是否|是不是)(?:意味着|说明|证明)(?:这个|该)分子'
            r'(?:一定|必然)(?:会)?(?:导致|引起|造成)(?:人体)?(?:肝损伤|肝毒性)[？?]'
            r'\s*还需要哪些实验(?:来)?验证[？?。]?', self.query.strip(), re.I)
        named_endpoint_question = re.fullmatch(
            r'(?:刚才|上一轮|上次)\s*(?P<smiles>\S+)\s*的\s*'
            r'(?P<endpoint>[A-Za-z][A-Za-z0-9_-]{0,99})\s*预测值是多少[？?。]?',
            self.query.strip(), re.I)
        if named_endpoint_question and enable_tools:
            from src.agent.candidate_evidence_followup import _canonical
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            canonical = _canonical(named_endpoint_question.group('smiles'))
            if canonical and isinstance(evidence, dict) and evidence.get('session_id') == self.session_id:
                for row in evidence.get('candidates', []):
                    if (row.get('canonical_smiles') != canonical or not any(
                            source.get('tool_name') == 'admet_predictor' and source.get('evidence_id')
                            for source in row.get('sources', []))):
                        continue
                    endpoints = row.get('admet', {}).get('endpoints', {})
                    matches = [(key, value) for key, value in endpoints.items()
                               if key.casefold() == named_endpoint_question.group('endpoint').casefold()]
                    if len(matches) == 1:
                        key, endpoint = matches[0]
                        unit = '' if endpoint['unit'] in ('', '-', 'unknown') else ' ' + endpoint['unit']
                        self.entry['candidate_evidence'] = deepcopy(evidence)
                        return {'content': f"`{canonical}` 的 `{key}` 模型预测值为 {endpoint['value']:.4g}{unit}。",
                                'status': 'completed'}
            return {'content': '上一轮没有该分子可核对的端点预测记录，请提供结构并重新预测。',
                    'status': 'not_calculated'}
        endpoint_question = re.fullmatch(
            r'(?P<endpoint>[A-Za-z][A-Za-z0-9_-]{0,99})\s*的\s*预测值是多少[？?]'
            r'\s*(?:判定|筛查)?阈值是多少[？?。]?', self.query.strip(), re.I)
        endpoint_safety_question = re.fullmatch(
            r'(?:刚才|上一轮|上次)(?:的)?\s*'
            r'(?P<names>[A-Za-z][A-Za-z0-9_-]{0,99}(?:\s*(?:、|和|与|及|,)\s*'
            r'[A-Za-z][A-Za-z0-9_-]{0,99}){1,3})\s*'
            r'(?:的)?预测值分别是多少[？?]\s*'
            r'(?:这些|这两个)(?:结果|预测)(?:能否|能不能|能)证明'
            r'(?:该|这个)分子(?:对人体|在人体中)安全吗[？?。]?', self.query.strip(), re.I)
        if endpoint_safety_question:
            names = re.findall(r'[A-Za-z][A-Za-z0-9_-]{0,99}', endpoint_safety_question.group('names'))
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            rows = evidence.get('candidates') if isinstance(evidence, dict) else None
            if (not isinstance(evidence, dict) or evidence.get('session_id') != self.session_id
                    or not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
                    or not isinstance(rows[0].get('canonical_smiles'), str)
                    or not any(source.get('tool_name') == 'admet_predictor' and source.get('evidence_id')
                               for source in rows[0].get('sources', []))):
                return {'content': '无法确认上一轮唯一分子的 ADMET 预测记录，请重新提供结构并预测。',
                        'status': 'not_calculated'}
            row = rows[0]
            endpoints = row.get('admet', {}).get('endpoints', {})
            lines, missing = [], []
            for name in names:
                matches = [(key, detail) for key, detail in endpoints.items() if key.casefold() == name.casefold()]
                if len(matches) != 1:
                    missing.append(name)
                    continue
                key, detail = matches[0]
                unit = '' if detail['unit'] in ('', '-', 'unknown') else ' ' + detail['unit']
                kind = {'classification': '分类', 'regression': '回归'}.get(detail['task_type'], '模型')
                lines.append(f"{key}：{detail['value']:.4g}{unit}（{kind}预测）")
            if missing:
                lines.append('未找到可核对的预测端点：' + '、'.join(missing) + '。')
            self.entry['candidate_evidence'] = deepcopy(evidence)
            return {'content': (f"上一轮 `{row['canonical_smiles']}` 的模型结果：" + '；'.join(lines)
                    + '。这些预测不能证明该分子对人体安全；应结合模型适用范围、剂量与暴露，'
                      '以及相应实验和其他安全性证据判断。'),
                    'status': 'partial' if missing else 'completed'}
        liver_question = re.fullmatch(_LIVER_FOLLOWUP + r'[？?。]?',
                                      self.mixed_history_query or self.query.strip(), re.I)
        negative_liver_question = re.fullmatch(_NEGATIVE_LIVER_FOLLOWUP + r'[？?。]?',
                                               self.mixed_history_query or self.query.strip(), re.I)
        if risk_question or risk_detail_question or short_dili_question or scored_dili_question or dili_probability_question or scored_dili_probability or dili_validation_question or endpoint_question or liver_question or negative_liver_question:
            evidence = self.history[-1].get('candidate_evidence') if self.history else None
            named_question = risk_question or risk_detail_question or scored_dili_question or dili_probability_question or liver_question
            subject = named_question.group('subject') if named_question and not negative_liver_question else None
            if (isinstance(evidence, dict) and evidence.get('session_id') == self.session_id
                    and len(evidence.get('candidates', [])) == 1):
                row = evidence['candidates'][0]
                if subject and subject not in {'这个分子', '该分子', '它'}:
                    from src.agent.tools.base_tool import BaseMolecularTool
                    from src.system.molecular_input_parser import parse_molecular_inputs
                    try:
                        parsed = parse_molecular_inputs(evidence['query'], BaseMolecularTool('risk_followup', ''))
                    except ValueError:
                        parsed = []
                    structure = re.escape(parsed[0].smiles) if len(parsed) == 1 else ''
                    labeled_structure = (r'(?:预测|分析|计算)\s*' + re.escape(subject) +
                        r'\s*(?:（\s*(?:(?i:SMILES)\s*[:：=]\s*)?' + structure + r'\s*）|'
                        r'\(\s*(?:(?i:SMILES)\s*[:：=]\s*)?' + structure + r'\s*\)|'
                        r'(?i:SMILES)\s*[:：=]\s*' + structure + r'(?=$|[\s，,。；;]))')
                    if (len(parsed) != 1 or parsed[0].canonical_smiles != row.get('canonical_smiles')
                            or not re.search(labeled_structure, evidence['query'])):
                        answer = {'content': '无法确认上一轮该分子的 ADMET 风险证据，请重新提供结构并预测。',
                                  'status': 'partial'}
                        if self.mixed_history_query is not None:
                            self.mixed_history_view = answer
                            return None
                        return answer
                identity_notice = (' ' + UNVERIFIED_NAME_WARNING
                                   if subject and subject not in {'这个分子', '该分子', '它'} else '')
                admet = row.get('admet', {})
                if endpoint_question and not short_dili_question:
                    endpoints = admet.get('endpoints', {})
                    matches = [(key, value) for key, value in endpoints.items()
                               if key.casefold() == endpoint_question.group('endpoint').casefold()]
                    if len(matches) == 1 and any(source.get('tool_name') == 'admet_predictor'
                            and source.get('evidence_id') for source in row.get('sources', [])):
                        key, endpoint = matches[0]
                        unit = '' if endpoint['unit'] in ('', '-', 'unknown') else ' ' + endpoint['unit']
                        content = f"上一轮 `{key}` 的模型预测值为 {endpoint['value']:.4g}{unit}。"
                        self.entry['candidate_evidence'] = deepcopy(evidence)
                        if 'threshold' in endpoint:
                            state = '命中' if endpoint['hit'] else '未命中'
                            content += f"风险筛查阈值为 {endpoint['threshold']:.4g}，{state}阈值。"
                            return {'content': content + '这是模型预测，不能据此确认实验结果。', 'status': 'completed'}
                        return {'content': content + '当前记录没有可用于该端点的风险筛查阈值，无法判断阈值命中。', 'status': 'partial'}
                    return {'content': '上一轮没有可核对的该端点预测记录，无法给出数值或阈值。', 'status': 'partial'}
                if risk_detail_question or short_dili_question or scored_dili_question or dili_probability_question or scored_dili_probability or dili_validation_question:
                    detail = admet.get('dili_risk')
                    if (isinstance(detail, dict) and any(source.get('tool_name') == 'admet_predictor'
                            and source.get('evidence_id') for source in row.get('sources', []))):
                        scored = scored_dili_question or scored_dili_probability
                        if scored and float(scored.group('score')) != float(f"{detail['value']:.4g}"):
                            return {'content': f"所给数值与上一轮记录不一致；记录中的 DILI 分类预测值为 {detail['value']:.4g}。请核对后再问。" + identity_notice,
                                    'status': 'partial'}
                        self.entry['candidate_evidence'] = deepcopy(evidence)
                        state = '命中' if detail['hit'] else '未命中'
                        if dili_probability_question or scored_dili_probability:
                            return {'content': f"上一轮 DILI 分类模型预测值为 {detail['value']:.4g}，筛查阈值为 {detail['threshold']:.4g}，{state}阈值。"
                                    '不能将模型分数直接视为人体发生肝损伤的概率，也不能在未核验校准情况时将其视为模型置信度；需结合剂量、暴露和实验数据判断。' + identity_notice,
                                    'status': 'completed'}
                        validation = ('可结合暴露与剂量设计肝细胞毒性、代谢稳定性及相关机制实验，'
                                      '并根据研究阶段补充适当的体内和临床证据。' if dili_validation_question else '')
                        return {'content': f"上一轮肝损伤分类端点的预测值为 {detail['value']:.4g}，筛查阈值为 {detail['threshold']:.4g}，{state}阈值。"
                                '这是模型预测，不能证明该分子会或不会导致实验或临床肝损伤；应结合模型适用范围和实验结果判断。' + validation + identity_notice,
                                'status': 'completed'}
                    return {'content': '上一轮缺少可核对的肝损伤端点预测值或筛查阈值，无法给出数值；预测也不能证明实验或临床肝损伤。' + identity_notice,
                            'status': 'partial'}
                names, count = admet.get('risk_names'), admet.get('risk_count')
                if (isinstance(names, list) and count == len(names)
                        and any(source.get('tool_name') == 'admet_predictor' and source.get('evidence_id')
                                for source in row.get('sources', []))):
                    risks = '、'.join(names) if names else '无'
                    limitation = ('不能证明该分子没有肝毒性' if negative_liver_question else
                                  '不能证明该分子有肝毒性' if subject
                                  else '不能证明该分子在实验或临床中存在毒性')
                    self.entry['candidate_evidence'] = deepcopy(evidence)
                    answer = {'content': f"上一轮分子 `{row['canonical_smiles']}` 的 ADMET 风险筛查分类端点的阈值命中：{risks}。"
                              f'这是模型预测的筛查提示，{limitation}；'
                              '应结合端点定义、模型适用域和实验结果判断。' + identity_notice, 'status': 'completed'}
                    if self.mixed_history_query is not None:
                        self.mixed_history_view = answer
                        return None
                    return answer
            answer = {'content': '无法确认上一轮该分子的 ADMET 风险证据，请重新提供结构并预测。',
                      'status': 'partial'}
            if self.mixed_history_query is not None:
                self.mixed_history_view = answer
                return None
            return answer
        from src.agent.candidate_evidence_followup import followup_answer as candidate_followup
        candidate_view = candidate_followup(self.query, self.history, self.session_id)
        logger.info("candidate evidence follow-up history=%d candidate_entries=%d result=%s",
                    len(self.history), sum(isinstance(item, dict) and
                    isinstance(item.get('candidate_evidence'), dict) for item in self.history),
                    candidate_view is not None)
        if candidate_view is not None:
            if candidate_view.get('reused_evidence') is not None:
                self.entry['candidate_evidence'] = deepcopy(candidate_view['reused_evidence'])
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
        view = self._capture(result, context=context, reverse_count=reverse_count,
                             reverse_restricted=reverse_restricted)
        from src.agent.contracts.concept_request import parse_ic50_ki_consultation
        consultation = parse_ic50_ki_consultation(self.query)
        if (consultation and self.session_id
                and result.metadata.get('binding_profile') == 'medchat-web-v1'
                and result.outcome == RunOutcome.COMPLETED and not result.tool_results
                and isinstance(result.final_answer, str)):
            self.entry['ic50_ki_consultation'] = {'session_id': self.session_id,
                'ic50': consultation['ic50'], 'unit': consultation['unit']}
            if not all(term in result.final_answer.casefold() for term in ('底物浓度', 'km', '竞争')):
                answer = result.final_answer + '\n\n' + _IC50_KI_CONDITIONS
                view = {'final_answer': answer, 'summary_answer': answer,
                        'suppress_raw_answer': True, 'status': 'completed', 'warnings': []}
        if (re.search(r'pH', self.query, re.I) and re.search(r'pKa', self.query, re.I)
                and result.outcome == RunOutcome.COMPLETED and not result.tool_results
                and isinstance(result.final_answer, str)):
            answer = re.sub(
                r'pH\s*远离\s*pKa\s*时[，,]\s*离子态占主导(?:[，,]\s*而不是中性态)?',
                '仅凭 pH 远离 pKa 不能判断哪种形态占主导，需要区分酸和碱及 pH 的方向',
                result.final_answer, flags=re.I)
            if answer != result.final_answer:
                view = {'final_answer': answer, 'summary_answer': answer,
                        'suppress_raw_answer': True, 'status': 'completed', 'warnings': []}
        from src.agent.contracts.concept_request import _MEASURED_SELECTIVITY
        measured = _MEASURED_SELECTIVITY.fullmatch(self.query)
        followup = _AFFINITY_INFERENCE.fullmatch(self.query)
        memory = getattr(context, 'memory', [])
        if followup and memory:
            previous = memory[-1]['user']
            if re.fullmatch(r'刚才那两个数值统一成\s*nM\s*各是多少[？?]?', previous, re.I) and len(memory) > 1:
                previous = memory[-2]['user']
            candidate = _MEASURED_SELECTIVITY.fullmatch(previous)
            if candidate and {followup['first'].casefold(), followup['second'].casefold()} == {
                    candidate['target'].casefold(), candidate['off'].casefold()}:
                measured = candidate
        if followup and measured is None and result.outcome == RunOutcome.COMPLETED and not result.tool_results:
            answer = ('未找到可确认的对应测量数据。请提供化合物及两个靶点的具体数值、'
                      '指标（如 IC50 或 Kd）和实验条件，再判断这个倍数代表什么。')
            return {'final_answer': answer, 'summary_answer': answer,
                    'suppress_raw_answer': True, 'status': 'partial', 'warnings': []}
        if measured and result.outcome == RunOutcome.COMPLETED and not result.tool_results:
            from decimal import Decimal, localcontext
            scales = {'pm': Decimal('1e-12'), 'nm': Decimal('1e-9'),
                      'μm': Decimal('1e-6'), 'um': Decimal('1e-6'), 'mm': Decimal('1e-3')}
            target = Decimal(measured['target_value']) * scales[measured['target_unit'].casefold()]
            off = Decimal(measured['off_value']) * scales[measured['off_unit'].casefold()]
            if target <= 0 or off <= 0:
                answer = 'IC50 必须为正数，无法根据所给数值计算比值；请检查实验数据。'
            else:
                self.entry['measured_selectivity'] = {'session_id': self.session_id, 'query': measured.group(0)}
                with localcontext() as ctx:
                    ctx.prec = 6
                    ratio = off / target
                display_ratio = format(ratio, 'f') if -4 <= ratio.adjusted() <= 6 else format(ratio, 'g')
                answer = (('不能仅凭 IC50 比值确定结合亲和力相差多少。' if followup else '')
                          + f'根据你提供的实验数值，脱靶 {measured["off"]} 的 IC50 ÷ 靶点 {measured["target"]} 的 IC50 = {display_ratio} 倍。'
                          '该比值反映这两项测定的相对效力；解释时需确认测定方法和条件可比。'
                          'IC50 受实验条件影响，不能直接等同于结合亲和力，也不能单独证明体内选择性、安全性或临床疗效。'
                          '建议重复测量并报告剂量反应曲线和不确定度，结合相关脱靶谱、游离暴露和正交结合实验进一步验证。')
            return {'final_answer': answer, 'summary_answer': answer,
                    'suppress_raw_answer': True,
                    'status': 'partial' if target <= 0 or off <= 0 else 'completed',
                    'warnings': []}
        from src.agent.contracts.concept_request import _selectivity_planning_consultation
        if (_selectivity_planning_consultation(self.query)
                and result.outcome == RunOutcome.COMPLETED and not result.tool_results
                and isinstance(result.final_answer, str)):
            answer = result.final_answer
            same_substrate = re.compile(
                r'[^。.!！?？\n]*同一浓度[^。.!！?？\n]*同一(?:底物|配体)'
                r'[^。.!！?？\n]*可比[^。.!！?？\n]*')
            def correct_assay_conditions(match):
                if re.search(r'不能|无法|不足以|不保证', match.group()):
                    return match.group()
                return ('比较目标与脱靶实验时，应核对实验机制、底物和测定条件的可比性；'
                        '对于酶实验，还需分别核对底物浓度与各酶 Km 的关系，相同浓度本身不能保证结果可比')
            answer = same_substrate.sub(correct_assay_conditions, answer)
            same_conditions = re.compile(
                r'[^。.!！?？\n]*同一条件下(?:测定|测量)[^。.!！?？\n]*(?:目标|靶点)'
                r'[^。.!！?？\n]*(?:脱靶|反靶|对照)[^。.!！?？\n]*')
            answer = same_conditions.sub(
                '分别在经过验证且可比较的条件下测定目标与脱靶活性，报告各自条件后再比较选择性', answer)
            method_claim = re.compile(
                r'[^。.!！?？；;\n]*(?:SPR|ITC)[^。.!！?？；;\n]{0,100}'
                r'(?:确定|确认|解析|定位)[^。.!！?？；;\n]{0,30}(?:结合位点|结合模式)'
                r'(?:与结合模式)?', re.I)
            def correct_method(match):
                if re.search(r'不能|无法|不可|不足以|不应|不直接', match.group()):
                    return match.group()
                return ('可结合共晶结构、NMR 或定点突变等研究结合位点；'
                        'SPR 和 ITC 可支持结合及亲和力评估，但常规测量不能单独确定结合位点或结合模式')
            answer = method_claim.sub(correct_method, answer)
            answer = re.sub(
                r'(?:SPR\s*(?:/|和|与|及|、)\s*ITC|ITC\s*(?:/|和|与|及|、)\s*SPR)\s*'
                r'(?:等)?(?:测|测定|测量|确认|验证|评估)\s*(?:结合)?'
                r'(?:亲和力|动力学)\s*(?:和|与|及|/|、)\s*(?:结合)?(?:亲和力|动力学)'
                r'(?:\s*[（(]\s*k\s*[_-]?on\s*(?:/|和|与|及|、)\s*k\s*[_-]?off\s*[）)])?',
                'SPR 可在适当条件下估算结合动力学，常规 ITC 主要测定亲和力和热力学',
                answer, flags=re.I)
            docking_claim = re.compile(
                r'[^。.!！?？；;\n]*对接\s*[,，]?\s*(?:直接)?(?:确定|确认|证明)'
                r'[^。.!！?？；;\n]{0,30}(?:结合模式|结合姿势|结合位点)')
            def correct_docking_claim(match):
                if re.search(r'不能|无法|不可|不足以|不应|不直接', match.group()):
                    return match.group()
                return ('可利用共晶结构或 NMR 等实验研究结合模式；'
                        '分子对接只能提出待实验验证的结合姿势假设')
            answer = docking_claim.sub(correct_docking_claim, answer)
            docking_interpretation = re.compile(
                r'[^。.!！?？\n]*对接[^。.!！?？\n]*(?:了解|找出|识别|判断)'
                r'[^。.!！?？\n]*[。.!！?？]')
            def qualify_docking_interpretation(match):
                sentence = match.group()
                if re.search(r'假设|预测|待.*验证|实验验证|不能|无法', sentence):
                    return sentence
                return (sentence.replace('共晶/分子对接', '共晶或分子对接')
                        + '对接产生的姿势和相互作用是待实验验证的假设，不能单独确定结合模式或关键残基。')
            answer = docking_interpretation.sub(qualify_docking_interpretation, answer)
            intro_only = bool(re.search(
                r'以下(?:信息|内容|方面)[，,]\s*再(?:谈|讨论|说明|设计)(?:后续)?(?:实验|方案|实验设计)[。.!！]?\s*$',
                answer))
            if intro_only:
                answer += '\n\n本次未列出所需信息和实验步骤，请继续要求补充。'
            answer = '本次为通用实验规划，未分析所提供的结构；名称和具体药理信息尚未核验。\n\n' + answer
            view = {'final_answer': answer, 'summary_answer': answer,
                    'suppress_raw_answer': True, 'status': 'partial' if intro_only else 'completed', 'warnings': []}
        from src.agent.planning.request_parsing import positive_intent_text
        if (re.search(r'分辨率\s*(?:最高|最好|最优)|(?:最高|最好|最优)\s*(?:的)?\s*分辨率|'
                      r'\b(?:highest|best)\s+resolution\b', positive_intent_text(self.query), re.I)
                and any(item.tool_name == 'target_database_search' and item.success
                        and item.quality.get('validated') is True for item in result.tool_results)):
            view = view or {'warnings': []}
            notice = '当前检索结果不保证覆盖全部 PDB 记录，无法确认全库分辨率最高的结构；以下为已检索记录。'
            view['prefix'] = '\n\n'.join(filter(None, (view.get('prefix'), notice)))
            view.update(status='partial', success=False, partial=True)
        from src.agent.contracts.docking_intent import docking_readiness_requested
        if (docking_readiness_requested(self.query)
                and result.metadata.get('binding_profile') == 'medchat-web-v1'
                and result.outcome in {RunOutcome.COMPLETED, RunOutcome.PARTIAL}
                and any(item.tool_name == 'target_database_search' and item.success
                        and item.quality.get('validated') is True for item in result.tool_results)):
            view = view or {'warnings': []}
            view.setdefault('warnings', []).append(
                '结构记录未核验受体准备状态；需检查结构完整性、配体和口袋，并完成受体准备后才能判断。')
            view.update(status='partial', success=False, partial=True)
        if ((getattr(context, 'metadata', {}).get('scientific_plan_status') == 'ready'
             or result.metadata.get('binding_profile') == 'medchat-web-v1')
                and result.outcome in {RunOutcome.COMPLETED, RunOutcome.PARTIAL}
                and any(item.tool_name in {'property_calculator', 'admet_predictor'} and item.success
                        and item.quality.get('validated') is True for item in result.tool_results)
                and _has_unverified_molecule_name(
                    self.resumed_named_admet or (context.query if self.contextual_property else self.query),
                    result.tool_results)):
            view = view or {'status': result.outcome.value, 'warnings': []}
            if UNVERIFIED_NAME_WARNING not in view.get('warnings', []):
                view.setdefault('warnings', []).append(UNVERIFIED_NAME_WARNING)
        if (result.metadata.get('binding_profile') == 'medchat-web-v1'
                and result.success and result.outcome == RunOutcome.COMPLETED
                and not result.tool_results and not result.metadata.get('waiting_for_input')):
            # Carry the accepted record, never the model's interpretation.
            for context_key, evidence_key in (
                    ('prior_target_context', 'target_structure_evidence'),
                    ('prior_activity_context', 'activity_evidence'),
                    ('prior_candidate_context', 'candidate_evidence')):
                if getattr(context, 'metadata', {}).get(context_key) is not None:
                    self.entry[evidence_key] = deepcopy(self.history[-1][evidence_key])
            if getattr(context, 'metadata', {}).get('prior_activity_context') is not None:
                answer = (view or {}).get('final_answer', result.final_answer)
                displayed = activity_explanation_text(answer, self.query)
                if displayed != answer:
                    view = view or {'status': 'completed', 'warnings': []}
                    view.update(final_answer=displayed, summary_answer=displayed,
                                suppress_raw_answer=True)
            if getattr(context, 'metadata', {}).get('prior_candidate_context') is not None:
                answer = (view or {}).get('final_answer', result.final_answer)
                displayed = answer
                if not re.search(
                        r'完整(?:精度|原值|数值)|原始(?:精度|数值)|(?:不要|不|无需)(?:四舍五入|舍入)|'
                        r'保留(?:所有|全部)小数|(?:保留|精确到)\s*[一二三四五六七八九十\d]+位|'
                        r'\b(?:full|original)\s+precision\b|\bunrounded\b', self.query, re.I):
                    from .decision_chat import _display_prediction_value
                    numbers = {rendered: _display_prediction_value(endpoint['value'])
                        for row in context.metadata['prior_candidate_context']['candidates']
                        for endpoint in row.get('admet', {}).get('endpoints', {}).values()
                        for rendered in (str(endpoint['value']), *(format(endpoint['value'], f'.{digits}g')
                            for digits in range(5, 17)))}
                    numbers = {raw: shown for raw, shown in numbers.items() if raw != shown}
                    if numbers:
                        # Match accepted values and their directly rounded
                        # representations; leave unrelated numbers untouched.
                        displayed = re.sub(
                            r'(?<![A-Za-z0-9_./+\-])(?:' + '|'.join(map(re.escape, numbers))
                            + r')(?![A-Za-z0-9_]|\.\d)', lambda match: numbers[match[0]], displayed)
                if 'solubility_aqsoldb' not in self.query.casefold():
                    displayed = re.sub(r'(?<![A-Za-z0-9_])Solubility_AqSolDB(?![A-Za-z0-9_])',
                                       '水溶解度', displayed, flags=re.I)
                    displayed = re.sub(r'(水溶解度\s*[（(])\s*水溶解度\s*[，,]\s*',
                                       r'\1', displayed)
                if 'prior_candidate_context' not in self.query.casefold():
                    displayed = re.sub(r'候选分子上下文\s*[（(]\s*`?prior_candidate_context`?\s*[）)]'
                                       r'(?:里|中)记录的', '本会话已记录的', displayed, flags=re.I)
                    displayed = re.sub(r'(?<![A-Za-z0-9_])prior_candidate_context(?![A-Za-z0-9_])',
                                       '已有计算与预测记录', displayed, flags=re.I)
                displayed = re.sub(r'^这是聊天请求[，,]\s*', '', displayed)
                if displayed != answer:
                    view = view or {'status': 'completed', 'warnings': []}
                    view.update(final_answer=displayed, summary_answer=displayed,
                                suppress_raw_answer=True)
        if self.mixed_history_view is None or result.outcome == RunOutcome.CANCELLED:
            return view
        prior = self.mixed_history_view
        if result.outcome not in {RunOutcome.COMPLETED, RunOutcome.PARTIAL}:
            return {'final_answer': prior['content'], 'summary_answer': prior['content'],
                    'mixed_history_failure': True, 'status': result.outcome.value, 'warnings': []}
        view = view or {'status': result.outcome.value, 'warnings': []}
        if result.tool_results:
            if (not view.get('summary_answer') and result.metadata.get('scientific_plan_status') == 'ready'
                    and all(item.tool_name == 'property_calculator' and item.success
                            and item.quality.get('validated') is True for item in result.tool_results)):
                from .decision_chat import scientific_display_answer
                answer = scientific_display_answer(result.tool_results, concise=True,
                                                   metrics=result.metadata.get('metrics'))
                if answer:
                    view.update(final_answer=answer, summary_answer=answer)
            view['prefix'] = prior['content']
        else:
            content = prior['content'] + '\n\n' + (result.final_answer or result.message)
            view.update(final_answer=content, summary_answer=content)
        if prior['status'] != 'completed':
            view['status'] = 'partial'
        return view

    def _capture(self, result, *, context, reverse_count=10, reverse_restricted=False):
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
        from src.agent.candidate_evidence_followup import accepted_candidate_evidence, accepted_generated_candidate_evidence
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
        generated_evidence = accepted_generated_candidate_evidence(raw, query, self.session_id)
        if generated_evidence is not None:
            self.entry['generated_candidate_evidence'] = generated_evidence
        from src.agent.target_structure_followup import accepted_target_evidence, accepted_target_function_evidence
        target_evidence = accepted_target_evidence(candidate_snapshot, self.session_id)
        if target_evidence is not None:
            self.entry['target_structure_evidence'] = target_evidence
        target_function = accepted_target_function_evidence(candidate_snapshot, self.session_id)
        if target_function is not None:
            self.entry['target_function_evidence'] = target_function
        candidate_evidence = accepted_candidate_evidence(candidate_snapshot, query, self.session_id)
        logger.info("candidate evidence capture observations=%d accepted=%d candidates=%d",
                    len(raw['tool_result_sequence']), len(candidate_sequence),
                    len(candidate_evidence.get('candidates', [])) if candidate_evidence else 0)
        if candidate_evidence is not None:
            from .property_constraints import generation_scope_matches
            if generation_scope_matches(candidate_snapshot, candidate_evidence):
                candidate_evidence['generation_scope_verified'] = True
                activity = evidence_followup.accepted_generated_activity_evidence(
                    raw, candidate_evidence, query, self.session_id)
                if activity is not None:
                    candidate_evidence['generated_activity_evidence'] = activity
            self.entry['candidate_evidence'] = candidate_evidence
        if (result.success and result.outcome == RunOutcome.COMPLETED
                and re.search(r'实验(?:蛋白(?:质)?)?结构', self.query)
                and {item['tool_name'] for item in candidate_sequence} == {'target_database_search'}):
            from .decision_chat import web_scientific_display
            answer = web_scientific_display(result, concise=True, experimental_only=True,
                                           include_target_function=bool(re.search(r'功能|function', self.query, re.I)))
            if answer:
                missing = any(not any(isinstance(structure, dict)
                                      and structure.get('structure_type') == 'experimental'
                                      for structure in row.get('recommended_structures', []))
                              for item in candidate_sequence for row in item.get('data') or []
                              if isinstance(row, dict))
                if re.search(r'(?:说明|解释)[^。；;！？?]{0,40}(?:准备受体|受体准备|'
                             r'(?:用于)?(?:小分子|分子)?对接前(?:需要)?准备(?:什么|哪些(?:事项|步骤)))', self.query):
                    answer += ('\n\n受体选择与准备需核查蛋白链、目标构象和突变状态、'
                               '缺失残基及结构完整性、共晶配体和对接口袋，以及水分子与辅因子。'
                               '准备时需按具体体系处理质子化状态并检查结构；'
                               '可用共晶配体重对接评估流程。本次未准备受体，也未执行对接。')
                return {'final_answer': answer, 'summary_answer': answer,
                        'status': 'partial' if missing else 'completed',
                        'partial': missing, 'success': not missing}
        if (OPTIMIZATION_INTERPRETATION.search(self.query)
                and {'property_calculator', 'drug_likeness_assessment'} ==
                    {item['tool_name'] for item in candidate_sequence}
                and len(raw['tool_result_sequence']) == 2):
            return {'prefix': '针对该分子的优化建议尚未生成。',
                    'status': 'partial', 'success': False, 'partial': True, 'warnings': []}
        if re.search(r'(?:生物学)?功能', self.query) and any(
                item.get('tool_name') == 'target_database_search' for item in candidate_sequence):
            from .decision_chat import web_scientific_display
            answer = web_scientific_display(result, concise=True)
            if answer:
                from src.agent.target_structure_followup import structure_followup
                detail = (structure_followup('这个靶点的生物学功能是什么？',
                    [{'target_function_evidence': target_function}], self.session_id) if target_function else
                    {'content': '本次未取得可核对的单一靶点功能注释。', 'status': 'partial'})
                text = answer + '\n\n' + detail['content']
                incomplete = detail['status'] != 'completed'
                return {'final_answer': text, 'summary_answer': text,
                        'status': 'partial' if incomplete else result.outcome.value,
                        'success': not incomplete, 'partial': incomplete,
                        'warnings': ['靶点功能注释未返回。'] if incomplete else []}
        if (getattr(context, 'metadata', {}).get('scientific_plan_status') == 'ready'
                and context.workflow_name in {'web-property-v1', 'web-property-batch-v1', 'web-admet-v1', 'web-admet-batch-v1', 'web-property-admet-batch-v1'}
                and result.metadata.get('workflow_version') == context.workflow_name):
            # Reviewed compiler workflows do not produce a model-cited JSON
            # answer. Reuse their validated observations through the same
            # molecular identity/provenance gate as the ordinary Pi path.
            properties = property_comparison.accepted_property_evidence(raw, query, self.session_id)
            self.entry['agent_used'] = True
            if properties is not None:
                self.entry['property_evidence'] = properties
            batch_properties = property_comparison.accepted_batch_property_evidence(raw, query, self.session_id)
            if batch_properties is not None:
                self.entry['batch_property_evidence'] = batch_properties
            if (context.workflow_name == 'web-property-batch-v1' and result.success
                    and 'drug_likeness_assessment' in {item['tool_name'] for item in raw['tool_result_sequence']}):
                from .decision_chat import web_scientific_display
                answer = web_scientific_display(result, concise=True)
                if answer:
                    return {'final_answer': answer, 'summary_answer': answer, 'status': 'completed'}
            if {item['tool_name'] for item in raw['tool_result_sequence']} == {'property_calculator'}:
                view = (None if context.workflow_name == 'web-property-batch-v1' else
                        property_comparison.descriptor_result(
                            self.query, properties, self.history, self.session_id))
                if self.property_comparison_requested and result.success:
                    from .decision_chat import scientific_display_answer
                    answer = scientific_display_answer(result.tool_results, concise=True,
                                                       metrics=result.metadata.get('metrics'))
                    rows = [row for item in result.tool_results if item.tool_name == 'property_calculator'
                            for row in (item.data or ()) if isinstance(row, dict)]
                    comparison = property_comparison.batch_descriptor_comparison(
                        rows, ('logp',) if self.lipophilicity_comparison else result.metadata.get('metrics', ()))
                    if self.lipophilicity_comparison:
                        conclusions = {
                            'logP：第一个结构较高': '根据计算 logP，第一个结构更亲脂。',
                            'logP：第二个结构较高': '根据计算 logP，第二个结构更亲脂。',
                        }
                        conclusion = next((value for key, value in conclusions.items()
                                           if key in comparison), None)
                        if conclusion:
                            comparison += '\n\n' + conclusion
                        if comparison:
                            comparison += '\n\n计算 logP 不是水溶解度测量；电离状态和溶液条件也会影响实际表现。'
                    if answer:
                        incomplete = not comparison or '舍入精度不足' in comparison
                        view = {'final_answer': answer + ('\n\n' + comparison if comparison else ''),
                                'summary_answer': answer + ('\n\n' + comparison if comparison else ''),
                                'status': 'partial' if incomplete else 'completed',
                                'warnings': ['未能完成结果比较。'] if not comparison else []}
                if self.property_origin_requested and result.success:
                    from .decision_chat import scientific_display_answer
                    answer = scientific_display_answer(result.tool_results, concise=True,
                                                       metrics=result.metadata.get('metrics'))
                    if answer:
                        answer += '\n\n表中性质和描述符均由结构计算，非实测值。'
                        view = {'final_answer': answer, 'summary_answer': answer,
                                'status': 'completed', 'warnings': []}
                if (self.pka_partial_requested or self.ionization_partial_requested) and properties is not None and result.success:
                    from .decision_chat import scientific_display_answer
                    answer = scientific_display_answer(result.tool_results, concise=True,
                                                       metrics=result.metadata.get('metrics'))
                    if answer:
                        answer += ('\n\n本次未计算指定 pH 下的电离比例；需要可靠的 pKa、'
                                   '相关电离状态及适用条件。')
                        if self.pka_partial_requested:
                            answer += '本次也未预测 pKa。'
                        view = {'final_answer': answer, 'summary_answer': answer,
                                'status': 'partial', 'success': False, 'partial': True, 'warnings': []}
                if properties is not None and result.success:
                    from rdkit import Chem
                    molecule = Chem.MolFromSmiles(properties['canonical_smiles'])
                    if molecule is not None and len(Chem.GetMolFrags(molecule)) > 1:
                        from .decision_chat import scientific_display_answer
                        answer = scientific_display_answer(result.tool_results, concise=True,
                                                           metrics=result.metadata.get('metrics'))
                        if answer:
                            if view is None:
                                view = {'final_answer': answer, 'summary_answer': answer,
                                        'status': result.outcome.value, 'warnings': []}
                            note = ('这些数值基于输入的完整多组分结构，包含所有片段；未自动去盐或中和。'
                                    '计算 logP 不能直接当作游离碱的实验分配系数。')
                            for field in ('final_answer', 'summary_answer'):
                                view[field] += '\n\n' + note
                from .property_constraints import _accepted, property_constraint_report
                accepted = [item for item in candidate_sequence if _accepted(item, 'property_calculator')]
                rows = accepted[0]['data'] if len(accepted) == 1 and isinstance(accepted[0].get('data'), list) else []
                report = property_constraint_report(self.query, rows)
                if report:
                    from .decision_chat import scientific_display_answer
                    if view is None:
                        answer = scientific_display_answer(result.tool_results, concise=True,
                                                           metrics=result.metadata.get('metrics')) or ''
                        view = {'final_answer': answer, 'summary_answer': answer,
                                'status': result.outcome.value, 'warnings': []}
                    else:
                        oral_explained = False
                    for field in ('final_answer', 'summary_answer'):
                        view[field] += '\n\n' + report[0]
                    if report[1]:
                        view['warnings'].append('部分约束未验证。')
                        view.update(status='partial', success=False, partial=True)
                oral_explained = (self.property_explanation == 'oral_comparison'
                    and result.success and len(rows) == 2
                    and all(isinstance(row, dict) and isinstance(row.get('properties'), dict)
                            and all(type(row['properties'].get(key)) in (int, float)
                                    and math.isfinite(row['properties'][key]) for key in ('logp', 'tpsa'))
                            for row in rows))
                if oral_explained:
                    from .decision_chat import scientific_display_answer
                    answer = scientific_display_answer(result.tool_results, concise=True,
                                                       metrics=result.metadata.get('metrics'))
                    if answer:
                        explanation = ('LogP 描述中性形式的亲脂性，TPSA 反映拓扑极性表面积；'
                                       '两者可为渗透性和溶解度评估提供线索，但不能单独判断哪个分子的口服吸收更好。'
                                       '还需结合离子化状态、溶解度、溶出、转运及代谢等证据，并通过实验验证。')
                        if view is None:
                            view = {'final_answer': answer, 'summary_answer': answer,
                                    'status': result.outcome.value, 'warnings': []}
                        for field in ('final_answer', 'summary_answer'):
                            view[field] = view.get(field, answer) + '\n\n' + explanation
                efficacy_explained = self.property_explanation == 'efficacy_limit' and result.success and bool(rows)
                if efficacy_explained:
                    from .decision_chat import scientific_display_answer
                    answer = scientific_display_answer(result.tool_results, concise=True,
                                                       metrics=result.metadata.get('metrics'))
                    if answer:
                        if view is None:
                            view = {'final_answer': answer, 'summary_answer': answer,
                                    'status': result.outcome.value, 'warnings': []}
                        explanation = ('不能仅凭这些计算性质证明哪个分子的药效更好。'
                                       '它们描述分子的理化特征，不直接测量靶点结合、功能活性或临床疗效。'
                                       '需要明确比较的生物作用和实验条件，并结合相应的实验活性、药代及安全性证据。')
                        for field in ('final_answer', 'summary_answer'):
                            view[field] = view.get(field, answer) + '\n\n' + explanation
                    else:
                        efficacy_explained = False
                if not self.mixed_activity_discussion and (self.concept_calculation is not None or self.property_explanation is not None
                        or OPTIMIZATION_INTERPRETATION.search(self.query) or re.search(
                        r'(?:解释|说明)(?:(?:该|这个)指标的含义|(?:这些|上述|相关|该|其)?(?:数值|结果|性质|含义))', self.query)) and not (oral_explained or efficacy_explained or self.property_origin_requested):
                    # The reviewed calculation path does not currently call
                    # the model for interpretation. Never report the user's
                    # separate explanation obligation as completed.
                    if view is None:
                        from .decision_chat import scientific_display_answer
                        answer = scientific_display_answer(result.tool_results, concise=True,
                                                           metrics=result.metadata.get('metrics'))
                        if not answer:
                            return None
                        view = {'final_answer': answer, 'summary_answer': answer,
                                'warnings': []}
                    warning = '计算已完成，但结果解释尚未生成。'
                    view['warnings'] = [*view.get('warnings', []), warning]
                    if OPTIMIZATION_INTERPRETATION.search(self.query):
                        view['warnings'].append('针对该分子的优化建议尚未生成。')
                    if self.property_explanation == 'oral_screening_choice':
                        view['warnings'].append('仅凭这些描述符无法确定哪个分子更适合口服筛选；还需要筛选目标、溶解度、渗透性、代谢和安全性等相关证据。')
                    if self.concept_calculation is not None:
                        view['concept_question'] = self.concept_calculation[0]
                    if self.property_explanation is not None:
                        view['property_explanation'] = self.property_explanation
                    view.update(status='partial', success=False, partial=True)
                return view
            from src.agent.planning.request_parsing import positive_intent_text
            from .decision_chat import _requested_admet_endpoints, web_scientific_display
            requested_endpoints = _requested_admet_endpoints(self.resumed_named_admet or self.query)
            from .scientific_obligations import solubility_prediction_requested
            if solubility_prediction_requested(self.query):
                plan = result.metadata.get('scientific_plan', {})
                requested_smiles = (plan.get('canonical_smiles') or '').splitlines()
                if plan.get('status') == 'ready' and len(requested_smiles) == 2:
                    self.solubility_requested_smiles = requested_smiles
            explain_limitations = re.search(
                r'(?:指出|说明|解释|列出)[^。；;！？?\n]{0,80}(?:局限|限制|不确定性)',
                positive_intent_text(self.query))
            explain_endpoint = re.search(
                r'(?:解释|说明)[^。；;！？?\n]{0,80}(?:代表什么|意味着什么|含义|是什么意思)',
                positive_intent_text(self.query))
            explanation_targets = []
            if explain_endpoint:
                named = re.findall(r'[A-Za-z][A-Za-z0-9_-]*', explain_endpoint.group())
                if len(named) == 1:
                    explanation_targets = named
                    if named[0].casefold() not in {name.casefold() for name in requested_endpoints}:
                        requested_endpoints += (named[0],)
                elif not named and len(requested_endpoints) == 1:
                    explanation_targets = list(requested_endpoints)
                elif not named and re.search(r'它们|这些结果|上述结果', explain_endpoint.group()):
                    explanation_targets = list(requested_endpoints)
            bound_report = None
            if (context.workflow_name in {'web-admet-v1', 'web-property-admet-batch-v1'}
                    and 'property_calculator' in result.metadata.get('required_tools', [])):
                from .property_constraints import _accepted, property_constraint_report
                property_items = [item for item in candidate_sequence if _accepted(item, 'property_calculator')]
                property_rows = (property_items[0]['data'] if len(property_items) == 1
                                 and isinstance(property_items[0].get('data'), list) else [])
                bound_report = property_constraint_report(self.query, property_rows)
            if ('admet_predictor' in {item['tool_name'] for item in raw['tool_result_sequence']}
                    and (requested_endpoints or explain_limitations or explain_endpoint or bound_report)):
                answer = web_scientific_display(result, concise=True,
                                                requested_admet_endpoints=requested_endpoints,
                                                focused_admet_only=(self.solubility_requested_smiles is not None
                                                    or bool(re.search(r'只(?:看|比较|对比|显示|列出|关注|给出)',
                                                                      positive_intent_text(self.query)))))
                if answer:
                    from .property_constraints import _accepted
                    observations = [item for item in candidate_sequence if _accepted(item, 'admet_predictor')]
                    rows = [row for item in observations for row in item.get('data', []) if isinstance(row, dict)]
                    missing = [name for name in requested_endpoints if not rows or any(
                        not any(key.casefold() == name.casefold() and isinstance(endpoint, dict)
                                and endpoint.get('source') == 'admet_ai_model'
                                and endpoint.get('validity') != 'out_of_range'
                                for key, endpoint in row.get('admet', {}).get('endpoints', {}).items())
                        for row in rows)]
                    warnings = []
                    if missing:
                        answer += '\n\n模型未返回或无法使用请求端点：' + '、'.join(missing) + '。'
                        warnings.append('部分请求端点未返回。')
                    if self.solubility_requested_smiles is not None:
                        from src.agent.candidate_evidence_followup import solubility_prediction_comparison
                        comparison = solubility_prediction_comparison(candidate_evidence, self.session_id,
                                                                       self.solubility_requested_smiles)
                        if comparison:
                            answer += '\n\n' + comparison
                            if '舍入精度不足' in comparison:
                                warnings.append('预测值显示精度不足，无法比较。')
                        else:
                            answer += '\n\n缺少两者可核对的水溶解度预测，无法比较。'
                            warnings.append('水溶解度预测比较未完成。')
                    if (len(rows) > 1 and requested_endpoints
                            and re.search(r'(?:比较|对比)[^。；;！？?\n]{0,100}风险',
                                          positive_intent_text(self.query))):
                        answer += '\n\n表中分类预测分数用于模型筛查，不能直接作为人体发生风险的概率。'
                    if ('CYP3A4_Veith' in requested_endpoints
                            and re.search(r'(?:能否|是否|可以).{0,20}(?:人体|临床).{0,12}(?:概率|风险)',
                                          positive_intent_text(self.query))):
                        if ('CYP3A4_Veith' not in missing and all(
                                row.get('admet', {}).get('endpoints', {}).get('CYP3A4_Veith', {}).get('task_type') == 'classification'
                                for row in rows)):
                            answer += ('\n\n不能将 CYP3A4 抑制模型的分类分数直接当作人体不良反应概率。'
                                       '还需结合抑制实验、暴露水平及潜在药物相互作用等证据判断。')
                        else:
                            warnings.append('请求的端点数值解释未完成。')
                    if explain_limitations:
                        warnings.append('ADMET 预测已产生，但模型预测局限的说明尚未生成。')
                    if explain_endpoint:
                        unexplained = not explanation_targets
                        for target in explanation_targets:
                            endpoint_key = {'herg': 'hERG', 'dili': 'DILI'}.get(target.casefold())
                            target_rows = [row.get('admet', {}).get('endpoints', {}).get(endpoint_key) for row in rows]
                            validated = (endpoint_key is not None and target_rows
                                and not any(name.casefold() == endpoint_key.casefold() for name in missing)
                                and all(isinstance(endpoint, dict) and endpoint.get('task_type') == 'classification'
                                        and endpoint.get('source') == 'admet_ai_model' for endpoint in target_rows))
                            # ADMET-AI uses TDC classification tasks:
                            # https://tdcommons.ai/single_pred_tasks/tox/
                            if validated and endpoint_key == 'hERG':
                                answer += ('\n\nhERG 是针对通道阻断的模型分类预测分数，属于筛查信息；'
                                           '它不是实测 IC50，也不能直接当作人体心脏毒性的概率。'
                                           '应结合模型适用域、hERG 实验、暴露水平及其他安全性证据判断。')
                            elif validated and endpoint_key == 'DILI':
                                answer += ('\n\nDILI 是药物性肝损伤的模型分类预测分数，属于筛查信息；'
                                           '不能直接当作人体发生肝损伤的概率，也不能证明会或不会发生肝损伤。'
                                           '应结合模型适用域、剂量、暴露及相关实验和临床证据判断。')
                            else:
                                unexplained = True
                        if unexplained:
                            warnings.append('请求的端点数值解释未完成。')
                    if bound_report:
                        answer += '\n\n' + bound_report[0]
                        if bound_report[1]:
                            warnings.append('部分约束未验证。')
                    incomplete = bool(warnings)
                    return {'final_answer': answer, 'summary_answer': answer,
                            'status': 'partial' if incomplete else result.outcome.value,
                            'success': result.success and not incomplete,
                            'partial': result.partial or incomplete, 'warnings': warnings}
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
            from .property_constraints import generation_scope_matches
            if generation_scope_matches(eligible, candidate_evidence):
                candidate_evidence['generation_scope_verified'] = True
                generated_activity = evidence_followup.accepted_generated_activity_evidence(
                    eligible, candidate_evidence, query, self.session_id)
                if generated_activity is not None:
                    candidate_evidence['generated_activity_evidence'] = generated_activity
            self.entry['candidate_evidence'] = candidate_evidence
        if self.reverse_boundary is not None:
            reverse_snapshot = {**raw, 'tool_result_sequence': reverse_sequence}
            pointer = self.reverse_boundary.capture(reverse_snapshot, session_id=self.session_id)
            if pointer is not None:
                self.entry['reverse_target_evidence'] = pointer
        view = evidence_followup.comparison_answer(self.query, eligible, self.session_id)
        tools = {item['tool_name'] for item in raw['tool_result_sequence']}
        if tools == {'admet_predictor'} and candidate_evidence is not None:
            from .decision_chat import _requested_admet_endpoints, web_scientific_display
            requested = _requested_admet_endpoints(self.resumed_named_admet or self.query)
            if requested:
                from src.agent.planning.request_parsing import positive_intent_text
                answer = web_scientific_display(result, concise=True, requested_admet_endpoints=requested,
                    focused_admet_only=bool(re.search(r'只(?:看|比较|对比|显示|列出|关注|给出)',
                                                     positive_intent_text(self.query))))
                if answer:
                    rows = candidate_evidence['candidates']
                    missing = [name for name in requested if any(
                        name.casefold() not in {key.casefold() for key in row.get('admet', {}).get('endpoints', {})}
                        for row in rows)]
                    if missing:
                        answer += '\n\n模型未返回或无法使用请求端点：' + '、'.join(missing) + '。'
                    view = {'final_answer': answer, 'summary_answer': answer,
                            'status': 'partial' if missing else result.outcome.value,
                            'success': result.success and not missing,
                            'partial': result.partial or bool(missing),
                            'warnings': ['部分请求端点未返回。'] if missing else []}
        if activity is not None and re.search(r'适用范围|适用域|applicability\s+domain', self.query, re.I):
            # The activity result contract carries no validated per-structure
            # applicability-domain assessment. Preserve the prediction while
            # explicitly marking that separate requested result as missing.
            view = view or {'warnings': []}
            notice = '本次未提供该结构的模型适用范围评估，无法判断其是否处于适用域内。'
            view['prefix'] = '\n\n'.join(filter(None, (view.get('prefix'), notice)))
            view.update(status='partial', success=False, partial=True)
        if (activity is not None and re.search(
                r'(?:说明|解释|列出|指出).{0,16}(?:局限|限制|不确定性)', self.query)):
            view = view or {'status': raw['status'], 'warnings': []}
            explanation = ('预测局限：这些数值来自模型而非实验；活性分类由模型阈值定义，'
                           '不能据此证明实验中有或没有活性。模型误差及该结构是否处于适用域内'
                           '需要结合模型验证资料核查，并通过合适的实验验证。')
            view['prefix'] = '\n\n'.join(filter(None, (view.get('prefix'), explanation)))
        if tools == {'property_calculator'}:
            view = property_comparison.descriptor_result(
                self.query, properties, self.history, self.session_id) or view
            from src.agent.planning.request_parsing import positive_intent_text
            if re.search(r'(?:哪个|哪一个|哪种)更(?:疏水|亲脂)', positive_intent_text(self.query)):
                from src.agent.candidate_evidence_followup import hydrophobic_comparison
                comparison = hydrophobic_comparison(candidate_evidence, self.session_id)
                view = view or {'status': raw['status'], 'warnings': []}
                notice = comparison or '缺少两者可核对的计算 logP，无法完成疏水性比较。'
                view['prefix'] = '\n\n'.join(filter(None, (view.get('prefix'), notice)))
                if comparison is None:
                    view.update(status='partial', success=False, partial=True)
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
            view = view or {'status': raw['status'], 'warnings': []}
            view['prefix'] = '\n\n'.join(filter(None, (view.get('prefix'), constraints)))
        return view
