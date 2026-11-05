"""Explicit three-sentence formatting requests for ordinary Web answers."""
import re


THREE_SENTENCE_FEEDBACK = (
    'The last answer missed the requested sentence count. Answer the original question '
    'again in exactly three complete sentences of plain prose. Combine the conclusion '
    'with its reason in sentence one, state the evidence in sentence two, and preserve '
    'the main limitation in sentence three. Do not add a heading or a separate yes/no preface.')

_DIRECTIVE = re.compile(
    r'(?:^|[。！？!?，,；;：:\n])\s*(?:(?:这次|这回|本次|回答|回复|全文|答案)\s*)?'
    r'(?:请\s*)?(?:(?:将|把)(?:回答|回复|答案|结论|说明))?(?:只|仅|恰好|刚好)?'
    r'(?:用|写成|写|分成|分|限于|限|控制在|限制在|压缩成|压缩为|给我|给出)\s*'
    r'(?:恰好|刚好|最多|不超过)?\s*[三3]\s*句(?:完整的?)?话?'
    r'|(?:^|[。！？!?，,；;：:\n])\s*(?:请)?[三3]句话?(?=回答|概括|说明|总结)'
    r'|(?:^|[.!?,;:\n])\s*(?:please\s+)?(?:answer|reply|respond|summari[sz]e|explain)\s+in\s+'
    r'(?:(?:exactly|only|at most|no more than)\s+)?(?:three|3)\s+sentences\b', re.I)


def three_sentence_range(query):
    query = re.sub(r'“[^”]*”|"[^"]*"|`[^`]*`', '', query)
    match = _DIRECTIVE.search(query)
    if not match:
        return None
    upper_bound = (re.search(r'最多|不超过|at most|no more than', match.group(), re.I)
                   or query[match.end():].startswith(('以内', '之内')))
    return range(1 if upper_bound else 3, 4)


def sentence_count(answer):
    # A period within a decimal, fingerprint or DOI is not a sentence ending.
    parts = re.split(r'''(?:[。！？!?]+|\.(?=\s|$))[”’」』"']*''', answer)
    return sum(bool(part.strip()) for part in parts)
