"""Interpret a complete row selection against an already displayed table."""
import re


ORDINAL = r'(?:[0-9]+|[一二三四五六七八九十百]+)'
_DIGITS = {digit: value for value, digit in enumerate('一二三四五六七八九', 1)}
_ROW_UNIT = r'(?:行|个(?:分子|候选)?)'
_ROW = re.compile(
    rf'(?:(?:不好意思|抱歉|说错了)[，,]\s*)?'
    rf'(?:不是\s*第\s*{ORDINAL}\s*{_ROW_UNIT}\s*[，,]\s*(?:而)?是\s*)?'
    rf'(?:其实)?(?:那|请|帮我)?'
    rf'(?:我指的是|我想看的是|改看|换成|看一下|看看|看)?\s*'
    rf'(?:(?:第\s*(?P<number>{ORDINAL})\s*|(?P<relative>最后一|上一|下一))'
    rf'{_ROW_UNIT}|(?P<current>它))(?:呢)?[。？?]?\s*')


def ordinal_number(number):
    if number.isascii():
        return int(number) if len(number) <= 2 else 0
    if number in _DIGITS:
        return _DIGITS[number]
    tens, separator, ones = number.partition('十')
    if separator and (not tens or tens in _DIGITS) and (not ones or ones in _DIGITS):
        return _DIGITS.get(tens, 1) * 10 + _DIGITS.get(ones, 0)
    return 0


def displayed_row_position(query, current=None, *, count=None):
    """Return a one-based position, including out-of-range requests, or None for another intent."""
    reply = query.strip()
    if count is not None and re.fullmatch(ORDINAL, reply):
        return ordinal_number(reply)
    match = _ROW.fullmatch(reply)
    if not match:
        return None
    if match['number']:
        return ordinal_number(match['number'])
    if match['current']:
        return current
    if match['relative'] == '最后一':
        return count
    if current is None:
        return 0
    return current + (1 if match['relative'] == '下一' else -1)
