"""Complete educational requests, never permission to execute named science."""
import re


_TOPIC = (
    r'(?:ADMET|ADME|(?:药物)?分子设计|药物设计|分子对接|反向寻靶|'
    r'理化性质|类药性|分子活性|logP|TPSA|QED|分子量|'
    r'(?:drug|molecular)\s+design|molecular\s+docking|'
    r'pIC50|IC50|活性概率|活性分类|分类(?:与|和)回归不一致|'
    r'activity\s+(?:probability|classification)|'
    r'classification(?:/|\s+and\s+)regression\s+(?:disagreement|inconsistency))'
)
_EXPLANATION = re.compile(
    rf'(?:(?:请\s*)?(?:什么是|解释(?:一下)?|介绍(?:一下)?|'
    rf'what\s+(?:is|are)\s+|(?:please\s+)?explain\s+)\s*(?:the\s+)?{_TOPIC}'
    rf'\s*(?:是什么意思|是什么|的含义|的概念|意味着什么)?|'
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
    rf'is\s+{_THRESHOLD}\s+a\s+universal\s+activity\s+threshold\s+across\s+targets', re.I,
)
_THRESHOLD_PREMISE = re.compile(rf'(?:你刚才说\s*)?{_THRESHOLD}\s*值得关注', re.I)
_APPLICABILITY_QUESTION = re.compile(rf'这{_APPLICABILITY}', re.I)


def is_concept_explanation(query):
    """All clauses must explain known concepts or forbid execution.

    Do not strip arbitrary negative tails: a concept prefix must not hide a
    candidate, requested value, prior-result reference or another operation.
    """
    if not isinstance(query, str):
        return False
    clauses = [clause.strip() for clause in re.split(
        r'[，,；;。!?！？\n]|\.(?=\s|$)', query) if clause.strip()]
    paired_threshold = (any(_THRESHOLD_PREMISE.fullmatch(clause) for clause in clauses)
                        and any(_APPLICABILITY_QUESTION.fullmatch(clause) for clause in clauses))
    explained = paired_threshold
    for clause in clauses:
        if _EXPLANATION.fullmatch(clause) or _THRESHOLD_QUESTION.fullmatch(clause):
            explained = True
        elif _READ_ONLY.fullmatch(clause):
            continue
        elif paired_threshold and (_THRESHOLD_PREMISE.fullmatch(clause)
                                   or _APPLICABILITY_QUESTION.fullmatch(clause)):
            continue
        else:
            return False
    return bool(explained)
