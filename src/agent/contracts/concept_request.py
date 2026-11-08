"""Complete educational requests, never permission to execute named science."""
import re


_TOPIC = (
    r'(?:ADMET|ADME|(?:药物)?分子设计|药物设计|分子对接|反向寻靶|'
    r'理化性质|类药性|分子活性|logP|logD|TPSA|QED|分子量|'
    r'(?:drug|molecular)\s+design|molecular\s+docking|'
    r'pIC50|IC50|活性概率|活性分类|分类(?:与|和)回归不一致|'
    r'activity\s+(?:probability|classification)|'
    r'classification(?:/|\s+and\s+)regression\s+(?:disagreement|inconsistency))'
)
_EXPLANATION = re.compile(
    rf'(?:(?:请\s*)?(?:简单\s*)?(?:什么是|解释(?:一下)?|介绍(?:一下)?|'
    rf'what\s+(?:is|are)\s+|(?:please\s+)?explain\s+)\s*(?:the\s+)?{_TOPIC}'
    rf'\s*(?:是什么意思|是什么|的含义|的概念|意味着什么|'
    rf'在药物设计中(?:是)?(?:什么意思|什么|有何意义|的作用))?|'
    rf'{_TOPIC}\s*(?:是什么|是什么意思)|'
    rf'what\s+does\s+(?:the\s+)?{_TOPIC}\s+mean)', re.I,
)
_READ_ONLY = re.compile(
    r'(?:请\s*)?(?:不要|不用|无需|不必)(?:再|重新)?(?:计算|重算|预测(?:任何分子)?|运行科学工具)|'
    r'(?:please\s+)?(?:do\s+not|don[’\x27]t)\s+(?:recalculate|recompute|rerun(?:\s+(?:tools|predictions|calculations))?)|'
    r'(?:please\s+)?do\s+not\s+predict\s+any\s+molecules|'
    r'(?:请\s*)?(?:只|仅)解释(?:判断依据|概念|原理)|'
    r'(?:也)?(?:请)?不要把预测当作实验结论|'
    r'(?:model\s+)?predictions?\s+are\s+not\s+experimental\s+(?:results?|conclusions?)', re.I,
)
# A user-supplied threshold is a discussion premise, not a computed candidate
# value. Pair an anaphoric applicability question with its explicit premise;
# neither a bare threshold nor "is this universal?" identifies a concept alone.
_THRESHOLD = r'pIC50\s*(?:>=|<=|≥|≤|>|<|=)\s*[+-]?\d+(?:\.\d+)?'
_APPLICABILITY = r'(?:是否是(?:所有|不同)靶点通用的(?:有效性|活性)标准|是否适用于所有靶点)'
_THRESHOLD_QUESTION = re.compile(
    rf'{_THRESHOLD}\s*{_APPLICABILITY}|'
    rf'{_THRESHOLD}\s*(?:的)?阈值\s*(?:是否适用于所有靶点|适用于所有靶点吗)|'
    rf'is\s+{_THRESHOLD}\s+a\s+universal\s+activity\s+threshold\s+across\s+targets', re.I,
)
_THRESHOLD_PREMISE = re.compile(rf'(?:你刚才说\s*)?{_THRESHOLD}\s*值得关注', re.I)
_APPLICABILITY_QUESTION = re.compile(rf'这{_APPLICABILITY}', re.I)
_ACTIVITY_DISAGREEMENT_CHOICE = re.compile(
    r'(?:这两个结果(?:冲突|不一致)时|分类(?:和|与)回归不一致时)'
    r'(?:我)?(?:该|应该)(?:信|相信)哪个')
_ACTIVITY_CLASS_RESTATEMENT = re.compile(
    r'(?:你把(?:这个|该)分子(?:分成|判为)|(?:刚才|上一轮)(?:的)?分类结果(?:为|是))'
    r'(?:有活性|无活性)')
_ACTIVITY_VALUE_RESTATEMENT = re.compile(
    r'(?:但|而)?(?:回归)?\s*pIC50\s*(?:只有|为|是|=)\s*[+-]?\d+(?:\.\d+)?', re.I)
_LOG_COMPARISON = re.compile(
    r'(?:请\s*)?(?:(?:解释|说明)(?:一下)?\s*)?(logP|logD)\s*(?:和|与)\s*(logP|logD)'
    r'\s*(?:有什么|的)(?:区别|差异)', re.I)
_LOG_ANAPHOR = re.compile(r'它与\s*(logP|logD)\s*有什么区别', re.I)
_LOG_ABSORPTION = re.compile(r'为什么不能只凭\s*logP\s*判断口服吸收', re.I)
_LOGD_PREMISE = re.compile(
    r'\s*(?:在)?(?:药化设计|药物设计|先导优化)中[，,]\s*'
    r'如果(?:某|一个)(?:可电离|可离子化)(?:先导物|先导分子|化合物)的(?:计算)?\s*logP\s*'
    r'(?:是|为)\s*[+-]?\d+(?:\.\d+)?[，,]\s*'
    r'(?:能否|能不能)直接把(?:它|该值)当成\s*pH\s*\d+(?:\.\d+)?\s*的\s*logD[？?]\s*'
    r'(?:请)?解释区别[，,]\s*(?:以及)?(?:还)?需要(?:什么|哪些)数据[。.!！?？]?\s*', re.I)
_IC50_KI_CONSULTATION = re.compile(
    r'\s*(?:假设|已知)(?:一个)?可逆竞争性(?:酶)?抑制剂(?:在实验中)?的\s*IC50\s*'
    r'(?:为|是)\s*(?P<ic50>\d+(?:\.\d+)?)\s*(?P<unit>pM|nM|[μµu]M|mM)[，,]\s*'
    r'(?:但)?(?:我)?(?:没有|未)提供底物浓度(?:或|和|与)\s*Km[。.!！]\s*'
    r'(?:能否|能不能)直接认为\s*Ki\s*(?:是|为)\s*\d+(?:\.\d+)?\s*'
    r'(?:pM|nM|[μµu]M|mM)[？?]\s*需要(?:哪些|什么)条件才能估算[？?。]?\s*', re.I)
_IC50_KI_EQUIVALENCE = re.compile(
    r'\s*(?:一个)?(?:假设的|假设|已知的?)?(?:可逆)?竞争性(?:酶)?抑制剂'
    r'(?:在(?:某|该)?(?:酶)?实验中)?的\s*IC50\s*(?:为|是)\s*'
    r'(?P<ic50>\d+(?:\.\d+)?)\s*(?P<unit>pM|nM|[μµu]M|mM)[，,]\s*'
    r'(?:能|能否|可以|可否)(?:直接)?(?:把|将)(?:它|该值)(?:当成|作为|视为)\s*Ki\s*'
    r'(?:吗|么)?[？?]\s*(?:还)?需要(?:哪些|什么)条件[？?。.!！]?\s*', re.I)


def parse_ic50_ki_consultation(query):
    """Recognize a complete equivalence question without consuming extra tasks."""
    return _IC50_KI_CONSULTATION.fullmatch(query) or _IC50_KI_EQUIVALENCE.fullmatch(query)
_ORAL_PK_DIFFERENCE = re.compile(
    r'(?:请)?(?:解释(?:一下)?)?\s*口服吸收(?:率)?\s*(?:和|与)\s*(?:口服)?生物利用度'
    r'\s*(?:有什么区别|有何区别|的区别是什么)[？?。.!！]?', re.I)

# A complete user-provided assay comparison is a request to reason about
# those measurements, not an instruction to predict an activity endpoint.
_MEASURED_SELECTIVITY = re.compile(
    r'\s*(?:我在相同实验条件下|我|实验)测得化合物\s*[A-Za-z0-9_-]+\s*对靶点\s*(?P<target>[A-Za-z0-9_-]+)'
    r'\s*的\s*IC50\s*(?:为|是)\s*(?P<target_value>\d+(?:\.\d+)?)\s*(?P<target_unit>pM|nM|[μµu]M|mM)\s*[，,]'
    r'\s*对脱靶蛋白\s*(?P<off>[A-Za-z0-9_-]+)\s*的\s*IC50\s*(?:为|是)\s*(?P<off_value>\d+(?:\.\d+)?)\s*'
    r'(?P<off_unit>pM|nM|[μµu]M|mM)\s*[。.!！]\s*(?:请)?计算选择性倍数'
    r'(?:[，,]\s*(?:并)?(?:解释|说明)(?:结果|这个结果(?:能说明什么[、，,]不能说明什么|能和不能说明什么)))?'
    r'(?:[，,]\s*并提出下一步验证建议)?[。.!！]'
    r'(?:\s*不要把这些数据说成你自己预测或实验测得[。.!！]?)?\s*', re.I)

_MEASURED_KD_CLARIFICATION = re.compile(
    r'\s*化合物\s*[A-Za-z0-9_-]+\s*的\s*Kd\s*(?:是|为)\s*'
    r'(?P<target>[A-Za-z0-9_-]+)\s*(?P<target_value>\d+(?:\.\d+)?)\s*(?P<target_unit>pM|nM|[μµu]M|mM)\s*[、，,]'
    r'\s*(?P<off>[A-Za-z0-9_-]+)\s*(?P<off_value>\d+(?:\.\d+)?)\s*(?P<off_unit>pM|nM|[μµu]M|mM)'
    r'\s*[，,]均由同一缓冲体系下平衡结合实验测得[。.!！]\s*请据此回答刚才的问题[。.!！]?\s*', re.I)


def is_oral_pk_difference(query):
    return isinstance(query, str) and _ORAL_PK_DIFFERENCE.fullmatch(query.strip()) is not None


def _herg_assay_consultation(query):
    """Recognize a complete assay-vs-prediction question, not a new assay call."""
    patterns = {
        'assay': (r'(?:假设)?(?:我补充的)?实验报告(?:称|显示)\s*[:：]\s*'
                  r'(?:刚才|上一轮)(?:这|那)个分子在膜片钳实验中\s*hERG\s*IC50\s*'
                  r'(?:=|为|是)\s*\d+(?:\.\d+)?\s*(?:pM|nM|[μµu]M|mM)'),
        'comparison': r'(?:这|这个结果)(?:和|与)(?:刚才|上一轮)的预测(?:是否|是不是)(?:冲突|矛盾|不一致)',
        'missing': r'(?:我)?(?:还)?(?:没有|未)提供(?:实验条件|原始报告)(?:(?:或|和|及)(?:实验条件|原始报告))?',
        'question': r'(?:请)?(?:说明|解释)(?:应|应该)?(?:如何|怎样)(?:核对|核实)',
        'no_prediction': r'(?:请)?(?:先|暂时)?(?:不要|无需|不用)(?:重新|再次)?预测',
        'unverified': r'(?:也)?不要把(?:这条|这个|该)(?:假设|报告|结果)(?:当作|视为)已核验(?:的)?事实',
    }
    found = set()
    for clause in filter(None, (part.strip() for part in re.split(r'[，,。；;!?！？\n]|\.(?=\s|$)', query))):
        matches = [name for name, pattern in patterns.items() if re.fullmatch(pattern, clause, re.I)]
        if not matches:
            return False
        found.update(matches)
    return {'assay', 'comparison', 'question', 'no_prediction'} <= found


def _selectivity_planning_consultation(query):
    """Route a complete, explicitly read-only lead-planning question to chat."""
    patterns = {
        'lead': r'(?:我有|已有)(?:一个)?(?:先导化合物|先导分子)\s*SMILES\s*[:：]\s*[A-Za-z0-9@+\-\[\]()=#\\/%.:*]{1,512}',
        'goal': r'(?:希望|想|计划)(?:提高|改善)(?:它|其|该分子)?对(?:目标蛋白|靶点|目标靶点)的选择性',
        'missing': r'(?:现在|目前)?(?:没有|尚未|未)(?:提供)?(?:靶点|目标)(?:或|和|与)(?:活性|实验)数据',
        'question': r'(?:请)?(?:先)?(?:说说|说明|解释)(?:还)?需要(?:什么|哪些)信息(?:和|以及)(?:如何|怎样)设计(?:后续)?实验',
        'no_generation': r'(?:请)?(?:暂时)?(?:不要|不需要|无需)(?:生成|设计)(?:新)?(?:分子|化合物)',
    }
    found = set()
    for clause in filter(None, (part.strip() for part in re.split(r'[，,。；;!?！？\n]', query))):
        matches = [name for name, pattern in patterns.items() if re.fullmatch(pattern, clause, re.I)]
        if not matches:
            return False
        found.update(matches)
    return {'lead', 'goal', 'question', 'no_generation'} <= found


def _activity_priority_consultation(query):
    """Discuss existing predictions for experiment planning, without new tools."""
    target = r'(?:人源|人类)?\s*[A-Za-z][A-Za-z0-9_-]*\s*实验'
    patterns = {
        'source': r'(?:基于|根据)(?:刚才|上述|之前)(?:的)?(?:预测)?结果',
        'priority': (r'(?:我)?(?:应该|应当)?(?:先|优先)把(?:哪个|哪一个)(?:分子)?(?:送去)?做' + target
                     + r'|(?:这两个|这些)分子(?:哪个|哪一个)(?:更)?(?:值得|应该)优先做' + target),
        'comparison': (r'(?:我注意到)?(?:但|而)?第[一二三四五六七八九十\d]+个(?:分子)?(?:的)?\s*'
                       r'(?:pIC50|活性概率|分类分数)\s*(?:更高|更低)'),
        'explanation': r'(?:请)?(?:解释|说明)(?:这种差别|排序差异)(?:能否|是否)(?:支持|用于)(?:实验)?优先级',
    }
    found = set()
    for clause in filter(None, (part.strip() for part in re.split(r'[，,。；;!?！？\n]', query))):
        if _READ_ONLY.fullmatch(clause):
            found.add('read_only')
            continue
        matches = [name for name, pattern in patterns.items() if re.fullmatch(pattern, clause, re.I)]
        if not matches:
            return False
        found.update(matches)
    return {'source', 'priority', 'read_only'} <= found


def selectivity_planning_model_query(query):
    """Build a general-method view without inferring identity from a raw structure."""
    if not isinstance(query, str) or not _selectivity_planning_consultation(query):
        return None
    return re.sub(
        r'SMILES\s*[:：]\s*[A-Za-z0-9@+\-\[\]()=#\\/%.:*]{1,512}',
        '（用户已提供，本轮未分析结构）', query, count=1, flags=re.I
    ) + (' 用户已提供结构，无需重新提供结构；本轮仅给出通用规划，不要推断该结构的名称、药理作用或靶点。'
         '比较不同蛋白实验时，不要声称使用相同底物或相同浓度就足以保证可比；'
         '酶实验应分别核对底物浓度与各酶 Km 的关系、实验机制和其他条件。')


_LOG_SIGNIFICANCE = re.compile(r'为什么它们对(?:药物设计|先导化合物优化)重要', re.I)
_LOG_LIMITATIONS = re.compile(
    r'只凭\s*logP\s*(?:较|更)高[，,]\s*能确定(?:一个|该)分子'
    r'(?:更易通过血脑屏障|代谢更快|溶解度更低)'
    r'(?:[、，,]|且|和|以及)(?:更易通过血脑屏障|代谢更快|溶解度更低)'
    r'(?:(?:[、，,]|且|和|以及)(?:更易通过血脑屏障|代谢更快|溶解度更低))*吗[？?]'
    r'\s*为什么[？?]?', re.I)
_LIPINSKI_ABSORPTION = re.compile(
    r'(?:符合|满足)\s*Lipinski\s*(?:五规则|规则)\s*(?:就)?能(?:否)?保证'
    r'(?:这个|该)?分子口服吸收(?:好|良好)?(?:吗|么)?[？?。.!！]?'
    r'\s*(?:请?解释原因[。.!！?？]?)?', re.I)
_PROPERTY_EFFICACY_LIMIT = re.compile(
    r'(?:这些|上述|所计算的)(?:计算)?(?:理化)?性质能(?:否)?直接证明'
    r'(?:哪一种|哪个分子|它们)(?:的)?药效(?:更好|更强)?吗[？?]'
    r'\s*(?:请)?(?:解释|说明)(?:判断)?(?:依据|原因)[。.!！?？]?')
_DILI_SCORE_INTERPRETATION = re.compile(
    r'(?:刚才|之前|上一轮)\s*DILI\s*的(?:预测值|预测分数)\s*'
    r'(?P<score>0(?:\.\d+)?|1(?:\.0+)?)\s*是否意味着(?:服用|用药)后有\s*'
    r'(?:\d{1,3}(?:\.\d+)?)\s*%\s*的概率发生药物性肝损伤[？?。.!！]?\s*', re.I)
_TOXICITY_SCORE_PREMISE = re.compile(
    r'(?:(?:第[一二三四五六七八九十0-9]+个(?:分子)?|刚才|上一轮)(?:的)?\s*)?'
    r'(?:hERG|DILI)\s*(?:的)?(?:预测)?(?:分数|值)\s*(?:为|是)?\s*'
    r'(?:0(?:\.\d+)?|1(?:\.0+)?)', re.I)
_TOXICITY_THRESHOLD_PREMISE = re.compile(
    r'第[一二三四五六七八九十0-9]+个(?:分子)?(?:的(?:分数|预测值))?'
    r'(?:低于|小于|高于|大于)\s*(?:0(?:\.\d+)?|1(?:\.0+)?)')
_TOXICITY_SCORE_QUESTION = re.compile(
    r'(?:是不是|是否)(?:就)?(?:表示|意味着)(?:人体|用药后|服用后)'
    r'发生(?:药物性)?(?:肝损伤|心脏毒性|心律失常)的概率(?:为|是)\s*\d+(?:\.\d+)?\s*%|'
    r'(?:是不是|是否)(?:就)?(?:可以)?认为(?:没有|有)(?:肝毒性|心脏毒性)', re.I)

# Reading already-produced uncertainty records is not authorization to run
# an uncertainty estimator. Every other clause must still be read-only.
_UNCERTAINTY_ITEM = r'(?:置信区间|置信度|适用域(?:判定|评估|结果)?)'
_UNCERTAINTY_RECORD = re.compile(
    r'(?:请)?(?:给出|列出|核对|查看)(?:模型|预测模型)?'
    r'(?:已经|已|之前|刚才)(?:计算|记录|保存)的\s*'
    + _UNCERTAINTY_ITEM + r'(?:\s*(?:和|及|与|、)\s*' + _UNCERTAINTY_ITEM + r')*')
_UNCERTAINTY_CONTEXT = re.compile(
    r'(?:这(?:个|两个|些)|上述)(?:分子|候选分子)的预测(?:各)?(?:有多可靠|是否可靠)|'
    r'(?:如果|若)没有记录就(?:直接)?(?:说没有|说明没有)|'
    r'(?:请)?不要把分类分数(?:当作|当成|当)置信度')
_ACTIVITY_SCOPE_REPORT = re.compile(
    r'(?:这些|上述|刚才的)\s*(?:[A-Za-z][A-Za-z0-9_-]*\s*)?(?:活性)?(?:预测)?结果'
    r'(?:(?:能否|能不能|是否可以)直接(?:作为|当作)\s*(?:人源|人类)\s*[A-Za-z][A-Za-z0-9_-]*\s*'
    r'的实验(?:效力|活性)(?:写进|写入|用于)报告|'
    r'(?:能|能否|能不能)说明(?:它|该分子)对\s*(?:人源|人类)\s*'
    r'[A-Za-z][A-Za-z0-9_-]*\s*有活性(?:吗)?)', re.I)
_ACTIVITY_SCOPE_CONTEXT = re.compile(
    r'(?:请)?(?:结合|根据)(?:刚才|上述)(?:两个|这些|该)?分子的原始(?:活性)?(?:预测|结果)'
    r'(?:说明|解释)(?:依据和限制|依据与局限)|'
    r'(?:也)?不要把家族模型(?:当作|当成)亚型模型')
_REPORT_SUMMARY = re.compile(
    r'(?:请)?(?:用[一二两三四五六七八九十\d]+句话)?(?:概括|总结|简述)'
    r'(?:这些|上述|刚才的)结果的报告(?:写法|表述)(?:和|与)(?:限制|局限)')
_REPORT_RETAIN_VALUES = re.compile(
    r'(?:请)?保留(?:两个|这些|上述|刚才的)分子的(?:原始)?预测(?:数值|结果)')


def _model_evidence_consultation(query):
    """Accept complete model-evidence questions, never arbitrary clause tails."""
    # The object of this complete single-sentence instruction is literal text.
    if re.fullmatch(r'\s*(?:请)?(?:原样)?复述(?:这句话|以下句子)\s*[:：]\s*'
                    r'[^。.!！?？；;\n]+[。.!！?？]?\s*', query):
        return True
    metric = r'(?:ROC-AUC|PR-AUC|AUC)'
    number = r'(?:0(?:\.\d+)?|1(?:\.0+)?)'
    model = r'(?:这个|该|刚才的?|上一轮的?)?模型'
    topic = r'(?:校准状态|校准曲线|置信区间|验证集指标)'
    patterns = {
        'premise': (rf'假设我(?:看到|读到)(?:一份)?(?:说明|报告)称{model}'
                    rf'(?:的)?验证集\s*{metric}\s*(?:为|是)\s*{number}'),
        'missing': r'(?:但|而)?我(?:还)?(?:没有|未)提供(?:来源|出处|原文)',
        'inference': (r'(?:能否|是否)(?:据此|由此)(?:说|认为)第[一二三四五六七八九十\d]+个分子'
                      r'有\s*\d+(?:\.\d+)?\s*%\s*的预测正确率'),
        'gap': r'(?:或者|或是)(?:两个|这些)\s*BBB\s*分数的差异(?:已|已经)(?:超过|超出)误差',
        'distinction': r'(?:请)?区分(?:这条|该)未核验说法[、与和]模型整体指标(?:和|与)单分子判断',
        'purpose': rf'为了(?:核实|核对){model}的{topic}',
        'documentation': (r'(?:我)?(?:应|应该)在(?:相关)?文献或模型说明中'
                          r'(?:检查|核对)(?:哪些|什么)(?:内容|资料|信息)'),
        'explanation': rf'(?:我)?(?:想|希望)理解{model}的{topic}(?:和|与){topic}分别说明什么',
        'english': (rf'(?:please\s+)?explain\s+whether\s+a\s+reported\s+{metric}\s+'
                    rf'of\s+{number}\s+establishes\s+confidence\s+for\s+the\s+'
                    r'(?:first|second)\s+molecule'),
        'read_only': (
            r'(?:也)?(?:请\s*)?(?:不要|不用|无需|不必|不)(?:再|重新)?'
            r'(?:计算|重算|预测(?:任何分子)?|(?:运行|执行)(?:任何)?(?:科学)?工具|检索|搜索)|'
            r'(?:请\s*)?(?:只|仅)(?:解释|说明|讨论)(?:核对方法)?|'
            r'(?:please\s+)?(?:do\s+not|don[’\x27]t)\s+'
            r'(?:recalculate|recompute|rerun|predict|search|run\s+(?:any\s+)?tools(?:\s+or\s+predictions)?)'),
    }
    found = set()
    for clause in filter(None, (part.strip() for part in re.split(
            r'[，,；;。!?！？\n]|\.(?=\s|$)', query))):
        matches = [name for name, pattern in patterns.items() if re.fullmatch(pattern, clause, re.I)]
        if not matches:
            return False
        found.update(matches)
    return 'read_only' in found and bool(
        {'inference', 'documentation', 'explanation', 'english'} & found)


def is_concept_explanation(query):
    """All clauses must explain known concepts or forbid execution.

    Do not strip arbitrary negative tails: a concept prefix must not hide a
    candidate, requested value, prior-result reference or another operation.
    """
    if not isinstance(query, str):
        return False
    if _model_evidence_consultation(query):
        return True
    if (_MEASURED_SELECTIVITY.fullmatch(query) or _MEASURED_KD_CLARIFICATION.fullmatch(query) or _selectivity_planning_consultation(query) or _LOG_LIMITATIONS.fullmatch(query.strip()) or _LIPINSKI_ABSORPTION.fullmatch(query.strip()) or _PROPERTY_EFFICACY_LIMIT.fullmatch(query.strip()) or _DILI_SCORE_INTERPRETATION.fullmatch(query.strip())
            or is_oral_pk_difference(query) or _LOGD_PREMISE.fullmatch(query)
            or parse_ic50_ki_consultation(query) or _herg_assay_consultation(query)
            or _activity_priority_consultation(query)):
        return True
    clauses = [clause.strip() for clause in re.split(
        r'[，,；;。!?！？\n]|\.(?=\s|$)', query) if clause.strip()]
    uncertainty_records = any(_UNCERTAINTY_RECORD.fullmatch(clause) for clause in clauses)
    activity_disagreement = any(_ACTIVITY_DISAGREEMENT_CHOICE.fullmatch(clause) for clause in clauses)
    activity_scope = any(_ACTIVITY_SCOPE_REPORT.fullmatch(clause) for clause in clauses)
    report_summary = any(_REPORT_SUMMARY.fullmatch(clause) for clause in clauses)
    toxicity_question = (any(_TOXICITY_SCORE_PREMISE.fullmatch(clause) for clause in clauses)
                         and any(_TOXICITY_SCORE_QUESTION.fullmatch(clause) for clause in clauses))
    paired_threshold = (any(_THRESHOLD_PREMISE.fullmatch(clause) for clause in clauses)
                        and any(_APPLICABILITY_QUESTION.fullmatch(clause) for clause in clauses))
    log_topics = {match.group(1).casefold() for clause in clauses
                  if (match := re.fullmatch(r'(logP|logD)\s*是什么', clause, re.I))}
    log_comparison = any((match := _LOG_COMPARISON.fullmatch(clause))
                         and match.group(1).casefold() != match.group(2).casefold()
                         for clause in clauses)
    explained = paired_threshold
    for clause in clauses:
        if clause == '在先导化合物优化中':
            continue
        if _EXPLANATION.fullmatch(clause) or _THRESHOLD_QUESTION.fullmatch(clause) or _LOG_ABSORPTION.fullmatch(clause):
            explained = True
        elif _READ_ONLY.fullmatch(clause):
            continue
        elif activity_disagreement and (
                _ACTIVITY_DISAGREEMENT_CHOICE.fullmatch(clause)
                or _ACTIVITY_CLASS_RESTATEMENT.fullmatch(clause)
                or _ACTIVITY_VALUE_RESTATEMENT.fullmatch(clause)):
            explained = True
        elif uncertainty_records and (_UNCERTAINTY_RECORD.fullmatch(clause)
                                      or _UNCERTAINTY_CONTEXT.fullmatch(clause)
                                      or _READ_ONLY.fullmatch(clause.removeprefix('也'))):
            explained = True
        elif activity_scope and (_ACTIVITY_SCOPE_REPORT.fullmatch(clause)
                                 or _ACTIVITY_SCOPE_CONTEXT.fullmatch(clause)):
            explained = True
        elif report_summary and (_REPORT_SUMMARY.fullmatch(clause)
                                 or _REPORT_RETAIN_VALUES.fullmatch(clause)):
            explained = True
        elif toxicity_question and (
                _TOXICITY_SCORE_PREMISE.fullmatch(clause) or _TOXICITY_THRESHOLD_PREMISE.fullmatch(clause)
                or _TOXICITY_SCORE_QUESTION.fullmatch(clause)
                or re.fullmatch(r'(?:请)?(?:只|仅)解释(?:刚才|上一轮|上述)的结果', clause)):
            explained = True
        elif paired_threshold and (_THRESHOLD_PREMISE.fullmatch(clause)
                                   or _APPLICABILITY_QUESTION.fullmatch(clause)):
            continue
        elif (match := _LOG_COMPARISON.fullmatch(clause)) and match.group(1).casefold() != match.group(2).casefold():
            explained = True
        elif (match := _LOG_ANAPHOR.fullmatch(clause)) and log_topics - {match.group(1).casefold()}:
            explained = True
        elif log_comparison and _LOG_SIGNIFICANCE.fullmatch(clause):
            continue
        else:
            return False
    return bool(explained)
