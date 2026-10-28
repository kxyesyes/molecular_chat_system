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
_NEGATED = re.compile(
    r'(?:不要|无需|不需要|不|禁止|别)\s*(?:再|重新)?(?:进行|运行|执行|做)?\s*(?:小分子|分子)?对接|'
    r"\b(?:do\s+not|don't|not|without)\s+(?:(?:run|perform|execute|start)\s+)?(?:molecular\s+)?dock(?:ing)?\b", re.I)
_STRONG_ACTION = re.compile(r'(?:把|将|让)\s*[^，,。；;!?！？\n]{1,100}?对接|'
                            r'对接\s*[A-Za-z][A-Za-z0-9@+\-\[\]()=#\\/%.]{1,}', re.I)


def explicit_docking_action(query: str) -> bool:
    """Keep independently requested actions after removing suitability questions."""
    if not isinstance(query, str):
        return False
    for clause in _CLAUSES.split(query):
        clause = _SUITABILITY.sub('', clause)
        clause = _QUESTION.sub('', clause)
        clause = _REFERENCE_QUESTION.sub('', clause)
        clause = _NEGATED.sub('', clause)
        if not _DOCKING.search(clause):
            continue
        if _STRONG_ACTION.search(clause):
            return True
        # Only a structure-selection purpose is non-executing. A separate
        # action remains visible even when another clause describes suitability.
        if _PURPOSE.search(clause) and _STRUCTURE.search(clause):
            continue
        return True
    return False
