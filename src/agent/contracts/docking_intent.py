"""Bounded action-versus-suitability classification for Web docking requests."""
import re


_CLAUSES = re.compile(r'[，,。；;!?！？\n]|然后|随后|并且|接着|(?:并|后)(?=(?:对接|执行|运行|进行|把|将|用))|'
                      r'\band\s+(?=(?:then\s+)?dock\b)', re.I)
_SUITABILITY = re.compile(
    r'(?:能否|是否(?:可以)?|可否|能不能)\s*(?:把|将)\s*'
    r'[^，,。；;!?！？\n]{1,100}?\s*用于\s*(?:小分子|分子)?对接', re.I)
_DOCKING = re.compile(r'对接|\bdock(?:ing)?\b', re.I)
_PURPOSE = re.compile(
    r'(?:适合|可用(?:于|来|作)|用于|suitable\s+for|for\s+use\s+in)', re.I)
_STRUCTURE = re.compile(r'结构|靶点|蛋白|structures?|proteins?|targets?', re.I)
_QUESTION = re.compile(r'(?:能否(?:继续)?|是否(?:可以)?|可否|能不能)\s*(?:继续)?(?:分子)?对接', re.I)
_REFERENCE_QUESTION = re.compile(r'(?:它|这(?:个|些)?结构)\s*(?:能否|能|是否可以|可以)\s*用于\s*(?:小分子|分子)?对接(?:吗|么)?', re.I)
_READINESS = re.compile(r'(?:结构|受体)(?:是否|是不是)(?:已|已经)?准备好(?:进行)?(?:小分子|分子)?对接(?=$|[，,。；;!?！？\n])', re.I)
_PREPARATION_QUESTION = re.compile(
    r'(?:说明|解释)(?:用于)?(?:小分子|分子)?对接前(?:需要)?准备(?:什么|哪些(?:事项|步骤))'
    r'(?=$|[，,。；;!?！？\n])', re.I)
_NEGATED = re.compile(
    r'(?:不要|无需|不需要|不|禁止|别)\s*(?:再|重新)?(?:进行|运行|执行|做)?\s*(?:小分子|分子)?对接|'
    r"\b(?:do\s+not|don't|not|without)\s+(?:(?:run|perform|execute|start)\s+)?(?:molecular\s+)?dock(?:ing)?\b", re.I)
_STRONG_ACTION = re.compile(r'(?:把|将|让)\s*[^，,。；;!?！？\n]{1,100}?对接|'
                            r'对接\s*[A-Za-z][A-Za-z0-9@+\-\[\]()=#\\/%.]{1,}', re.I)


_ENERGY_PROHIBITION = re.compile(
    r'(?:(?:没有|缺少|未提供|没有提供)\s*(?:真实|有效|完整)?\s*(?:docking|对接)\s*参数时?\s*[,，]?\s*)?'
    r'(?:不要|无需|不需要|禁止|别)\s*(?:给出?|报告|提供|展示|输出|编造)\s*'
    r'(?:docking\s+energ(?:y|ies)\b|对接(?:结合)?能)|'
    r"\b(?:do\s+not|don't|never)\s+(?:report|provide|give|invent)\s+(?:a\s+)?docking\s+energ(?:y|ies)\b", re.I)


def without_docking_energy_prohibition(query: str) -> str:
    return _ENERGY_PROHIBITION.sub('', query)


def docking_readiness_requested(query: str) -> bool:
    return isinstance(query, str) and _READINESS.search(query) is not None


def redocking_consultation(query: str):
    """Recognize a ligand identity/readiness question without swallowing actions."""
    return re.fullmatch(
        r'\s*(?P<ligand>[A-Za-z0-9]{1,12})\s*是什么(?:共晶)?配体[？?]\s*'
        r'(?:能否|是否可以|可以|能不能)(?:直接)?(?:用|使用)(?:这个|该)结构'
        r'(?:给|对)\s*[A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff -]{0,63}?'
        r'(?:做|进行)重对接(?:验证)?(?:吗)?[？?]\s*'
        r'(?:请)?(?:先)?说明(?:依据和还需检查的项目|还需要检查什么)[，,]\s*'
        r'(?:先|暂时)?(?:不要|别|无需)(?:运行|执行)(?:重|分子)?对接[。.!！]?\s*', query, re.I)


def docking_preparation_consultation(query: str) -> bool:
    """Recognize complete, read-only preparation requests with declared inputs."""
    clauses = re.split(r'[，,。；;!?！？\n]|和|与', query)
    found = set()
    patterns = {
        'receptor': r'(?:我有\s*)?(?:受体\s*)?(?:PDB\s*|(?:[A-Za-z][\w-]*\s*)?结构\s*)[0-9][A-Za-z0-9]{3}',
        'ligand': r'(?:我有\s*)?(?:配体\s*)?SMILES\s*[:：]\s*[A-Za-z0-9@+\-\[\]()=#\\/%.:*]{1,512}',
        'purpose': r'(?:我)?(?:想|计划|准备)(?:做|进行)(?:分子|小分子)?对接',
        'pocket': r'(?:但|现在|目前|还)*(?:不知道(?:结合)?口袋|(?:结合)?口袋(?:尚未|没有|未)确定)',
        'question': r'(?:请)?(?:说明|告诉我)?(?:分子)?对接前(?:还)?需要准备(?:什么|哪些(?:步骤|事项))',
        'no_execution': r'(?:先|暂时)?(?:不要|别|不需要|无需)(?:运行|执行|进行)(?:(?:分子)?对接)?',
    }
    for clause in filter(None, (part.strip() for part in clauses)):
        matches = [name for name, pattern in patterns.items() if re.fullmatch(pattern, clause, re.I)]
        if not matches:
            return False
        found.update(matches)
    return {'receptor', 'ligand', 'question', 'no_execution'} <= found


def explicit_docking_action(query: str) -> bool:
    """Keep independently requested actions after removing suitability questions."""
    if not isinstance(query, str):
        return False
    if redocking_consultation(query):
        return False
    from src.agent.target_structure_followup import parse_domain_suitability_question
    if parse_domain_suitability_question(query):
        return False
    clauses = _CLAUSES.split(without_docking_energy_prohibition(query))
    for index, clause in enumerate(clauses):
        clause = re.sub(r'是否建议\s*(?:docking\b|(?:分子)?对接)|'
                        r'(?:最)?适合后续\s*(?:docking\b|(?:分子)?对接)\s*的', '', clause, flags=re.I)
        clause = _SUITABILITY.sub('', clause)
        clause = _QUESTION.sub('', clause)
        clause = _REFERENCE_QUESTION.sub('', clause)
        clause = _READINESS.sub('', clause)
        clause = _PREPARATION_QUESTION.sub('', clause)
        clause = _NEGATED.sub('', clause)
        if not _DOCKING.search(clause):
            continue
        if (index == 0 and re.fullmatch(
                r'\s*(?:我想|我计划)针对(?:人源\s*)?[A-Za-z][A-Za-z0-9_-]*\s*做(?:小分子|分子)?对接\s*', clause)
                and re.search(r'(?:检索|查询|查找|搜索)[^。；;!?！？\n]{0,80}(?:蛋白|结构)',
                              '，'.join(clauses[1:]))):
            continue
        if (_STRUCTURE.search(query) and re.fullmatch(
                r'\s*(?:用于|供)(?:后续|以后|未来)?(?:进行)?(?:小分子|分子)?对接\s*', clause, re.I)):
            continue
        if _STRONG_ACTION.search(clause):
            return True
        # Only a structure-selection purpose is non-executing. A separate
        # action remains visible even when another clause describes suitability.
        if _PURPOSE.search(clause) and _STRUCTURE.search(clause):
            continue
        return True
    return False
