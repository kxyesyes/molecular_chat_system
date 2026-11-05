"""Select requests for read-only intent classification."""
import re


def asks_about_given_information(query):
    """Limit reconsideration of rejected plans to explicit evidence-interpretation questions."""
    return re.search(
        r'(?:仅|只)(?:凭|据|根据|依据)(?:上述|以上|这些|现有|所给)(?:信息|数据|结果|证据)'
        r'|(?:能否|能|可以|可否)(?:仅)?(?:由此|据此)(?:认定|断定|推断|判断|认为|证明|声称)'
        r'|\bbased (?:only|solely) on (?:these|the (?:given|supplied)) (?:data|results|observations|facts)\b',
        query, re.I) is not None


def mentions_assay_concentration(query):
    """An endpoint and a concentration justify reviewing a rejected activity request."""
    return (re.search(r'(?<![a-z0-9_])(?:[IE]C(?:50|₅₀)|K[di])(?![a-z0-9_])', query, re.I | re.ASCII) is not None
            and re.search(r'\d\s*[fpnumμµ]?M\b', query, re.I | re.ASCII) is not None)


_NO_TOOLS = re.compile(
    r'(?:请)?(?:不要|不|禁止|无需)(?:运行|使用|执行|调用)(?:任何|所有)?(?:科学|科研)?工具|'
    r'(?:please\s+)?(?:do\s+not|don[’\x27]t|never)\s+(?:run|use|execute|call)\s+'
    r'(?:any\s+)?(?:scientific\s+)?tools', re.I)


def tool_free_consultation_requested(query):
    if not isinstance(query, str):
        return False
    # Quoted examples are objects of the request, including their punctuation.
    query = re.sub(r'''"[^"]*"|“[^”]*”|‘[^’]*’|「[^」]*」|(?<!\w)'[^']*'(?!\w)|`[^`]*`''', '_', query)
    return any(
        _NO_TOOLS.fullmatch(clause.strip()) for clause in re.split(
            r'[，,；;。!?！？\n]|\.(?=\s|$)', query))
