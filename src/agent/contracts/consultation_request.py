"""Select requests for read-only intent classification."""
import re


def selected_row_summary(query):
    """A request to restate the chosen row, with no new labelled structure input."""
    return (re.search(r'(?:选中|所选)(?:的)?(?:那|这)?一?(?:行|个分子|候选)|(?:当前这?|这|那)一?行|它的', query) is not None
            and re.search(r'摘要|总结|概括|转述|复述|简述', query) is not None
            and not re.search(r'SMILES\s*[:：=]', query, re.I))


def asks_about_given_information(query, *, prior_activity_context=None, prior_candidate_context=None):
    """Recognize readback of a resolved row or explicitly supplied evidence."""
    if prior_activity_context and prior_activity_context.get('selected_smiles'):
        return True
    if prior_candidate_context and prior_candidate_context.get('selected_smiles') and selected_row_summary(query):
        return True
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


def selected_activity_values_requested(query):
    """Match a complete request for the selected row's two prediction values."""
    clauses = [part.strip() for part in re.split(r'[。！？!?；;，,\n]', query)]
    request = '，'.join(part for part in clauses if part and not _NO_TOOLS.fullmatch(part))
    return re.fullmatch(
        r'(?:请问|请)?(?:复述|列出|给出|显示|查看)?\s*(?:刚才|当前)?(?:选中|所选)(?:的)?'
        r'(?:那|这)?(?:一?个)?(?:候选分子|分子|候选|行)的\s*(?:SMILES\s*[、，和与及]\s*)?'
        r'(?:活性概率\s*(?:、|，|和|与|及)\s*(?:预测\s*)?pIC50|'
        r'(?:预测\s*)?pIC50\s*(?:、|，|和|与|及)\s*活性概率)'
        r'\s*(?:(?:各|分别)?是多少)?(?:，(?:两项|两个)?(?:数值)?(?:都|均)?保留(?:四|4)位有效数字)?',
        request, re.I) is not None


def tool_free_consultation_requested(query):
    if not isinstance(query, str):
        return False
    # Quoted examples are objects of the request, including their punctuation.
    query = re.sub(r'''"[^"]*"|“[^”]*”|‘[^’]*’|「[^」]*」|(?<!\w)'[^']*'(?!\w)|`[^`]*`''', '_', query)
    if re.search(r'(?:^|[。！？!?，,]\s*)只(?:给|写|输出)(?:[一二三四五六七八九十\d]+句)?(?:台词|问句|问题)'
                 r'[，,]\s*(?:不要|不)(?:解答|回答)(?:台词中|其中|里面)?的?问题[。.!！]?\s*$', query):
        return True
    return any(
        _NO_TOOLS.fullmatch(clause.strip()) for clause in re.split(
            r'[，,；;。!?！？\n]|\.(?=\s|$)', query))
