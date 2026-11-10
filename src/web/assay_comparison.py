"""Compare paired assay measurements supplied by the user."""

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
from fractions import Fraction
import re

_ID = r'[A-Za-z][A-Za-z0-9_-]{0,31}'
_VALUE = r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d{1,2})?'
_UNIT = r'(?:pM|nM|[μµu]M|mM)'
_ROW = re.compile(
    rf'\s*化合物\s*(?P<compound>{_ID})\s*对(?:靶点)?\s*(?P<first>{_ID})\s*'
    rf'(?:的\s*IC50)?\s*(?:为|是)\s*(?P<first_op>>=|<=|>|<|≥|≤)?\s*(?P<first_value>{_VALUE})\s*(?P<first_unit>{_UNIT})'
    rf'\s*[，,]\s*对(?:靶点)?\s*(?P<second>{_ID})\s*(?:的\s*IC50)?\s*'
    rf'(?:为|是)\s*(?P<second_op>>=|<=|>|<|≥|≤)?\s*(?P<second_value>{_VALUE})\s*(?P<second_unit>{_UNIT})\s*', re.I)
_REQUEST = re.compile(
    rf'\s*(?:请)?比较(?:各自|它们)?对\s*(?P<target>{_ID})\s*(?:相对于|相比于)\s*'
    rf'(?P<other>{_ID})\s*的选择性倍数[，,]\s*(?:并)?说明(?:谁|哪个化合物)'
    rf'对\s*(?P=target)\s*更有选择性[。.!！]?\s*', re.I)
_SCALES = {'pm': Decimal('1e-12'), 'nm': Decimal('1e-9'),
           'μm': Decimal('1e-6'), 'um': Decimal('1e-6'), 'mm': Decimal('1e-3')}


def compare_supplied_kd(measurements, question, session_id):
    values = {measurements[key].casefold():
              Decimal(measurements[key + '_value']) * _SCALES[measurements[key + '_unit'].casefold()]
              for key in ('target', 'off')}
    if (not session_id or not question or question['session_id'] != session_id or len(values) != 2
            or {question['first'].casefold(), question['second'].casefold()} != values.keys()):
        return {'content': '请将要比较的两个靶点和各自的 Kd 一起提供，目前无法确认上一问题。', 'status': 'partial'}

    first, second = question['first'], question['second']
    first_kd, second_kd = values[first.casefold()], values[second.casefold()]
    if min(first_kd, second_kd) <= 0:
        return {'content': 'Kd 必须为正数，请核对测量值。', 'status': 'partial'}
    with localcontext() as ctx:
        ctx.prec = 6
        ratio = (second_kd / first_kd).normalize()
    display_ratio = format(ratio, 'f') if -4 <= ratio.adjusted() <= 6 else format(ratio, 'g')
    if first_kd == second_kd:
        affinity = '两者的结合亲和力相同'
    else:
        direction = '较强' if first_kd < second_kd else '较弱'
        affinity = f'{first} 的结合亲和力{direction}，为 {second} 的 {display_ratio} 倍'
    return {'content': (f'按你提供的平衡结合实验数据，{second} 的 Kd ÷ {first} 的 Kd = {display_ratio} 倍。'
                        f'若这些测量可比，{affinity}。比较时还需核对实验方法、重复测量与不确定度。'),
            'status': 'completed'}


def compare_supplied_ic50(query):
    """Return a result only when the complete request and every row are understood."""
    parts = re.fullmatch(
        r'\s*我有一组(?:假设的)?(?:同条件|相同条件下的?)?酶实验数据[：:]'
        r'(?P<rows>[^。]+)。(?P<request>[^。]+[。.!！]?)\s*', query, re.I)
    if not parts:
        return None
    request = _REQUEST.fullmatch(parts['request'])
    rows = [_ROW.fullmatch(row) for row in re.split(r'[；;]', parts['rows'])]
    if not request or len(rows) < 2 or any(row is None for row in rows):
        return None
    target, other = request['target'], request['other']
    names = [row['compound'].casefold() for row in rows]
    if (target.casefold() == other.casefold() or len(set(names)) != len(names)
            or any({row['first'].casefold(), row['second'].casefold()}
                   != {target.casefold(), other.casefold()} for row in rows)):
        return None
    comparisons = []
    for row in rows:
        values = {row[side].casefold(): (
            Fraction(Decimal(row[side + '_value'])) * Fraction(_SCALES[row[side + '_unit'].casefold()]),
            row[side + '_op']) for side in ('first', 'second')}
        if any(value <= 0 for value, _ in values.values()):
            return {'content': 'IC50 必须为正数；请核对所给数据后再比较。', 'status': 'partial'}
        numerator, numerator_op = values[other.casefold()]
        denominator, denominator_op = values[target.casefold()]
        ratio = numerator / denominator
        if numerator_op and denominator_op:
            numerator_greater = numerator_op in ('>', '>=', '≥')
            denominator_greater = denominator_op in ('>', '>=', '≥')
            if numerator_greater == denominator_greater:
                comparisons.append((row['compound'], None, None, False, False))
            else:
                strict = numerator_op in ('<', '>') or denominator_op in ('<', '>')
                comparisons.append((row['compound'], ratio if numerator_greater else None,
                                    None if numerator_greater else ratio, strict, strict))
        elif numerator_op or denominator_op:
            op = numerator_op or denominator_op
            lower_bound = (op in ('>', '>=', '≥')) == bool(numerator_op)
            strict = op in ('<', '>')
            comparisons.append((row['compound'], ratio if lower_bound else None,
                                None if lower_bound else ratio, strict, strict))
        else:
            comparisons.append((row['compound'], ratio, ratio, False, False))
    lines = [f'按你提供的数值，选择性比值定义为 IC50({other}) / IC50({target})：']
    for name, lower, upper, lower_open, upper_open in comparisons:
        if lower is None and upper is None:
            lines.append(f'化合物 {name}：根据两个界限无法确定有效的单侧比值界限。')
            continue
        ratio = lower if lower is not None else upper
        with localcontext() as context:
            context.prec = 3
            context.rounding = ROUND_FLOOR if upper is None else ROUND_CEILING if lower is None else context.rounding
            rounded = Decimal(ratio.numerator) / Decimal(ratio.denominator)
        display = format(rounded, 'f') if -3 <= rounded.adjusted() <= 6 else str(rounded)
        operator = ('> ' if lower_open else '≥ ') if upper is None else (
            '< ' if upper_open else '≤ ') if lower is None else ''
        if lower is not None and upper is not None and Fraction(rounded) != ratio:
            operator = '约 '
        lines.append(f'化合物 {name}：{operator}{display} 倍。')
    winners = [candidate[0] for candidate in comparisons if candidate[1] is not None
               and all(other[2] is not None and
                       (candidate[1] > other[2] or
                        (candidate[1] == other[2] and (candidate[3] or other[4])))
                       for other in comparisons if other is not candidate)]
    if len(winners) == 1:
        lines.append(f'在所给数据下，化合物 {winners[0]} 对 {target} 相对于 {other} 的选择性比值最高。')
    else:
        lines.append('根据这些数值或界限无法确定哪一个选择性比值最高。')
    lines.append('这是对用户提供数值的计算，并非活性预测；跨靶点比较仍需核对物种、'
                 '底物及测定条件、重复数据和不确定度，IC50 比值不能直接等同于结合亲和力比值。')
    return {'content': '\n'.join(lines), 'status': 'completed'}
