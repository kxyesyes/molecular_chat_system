"""Audit explicitly supplied binary labels and positive-class probabilities."""
from collections import defaultdict
from fractions import Fraction
import re


_NUMBER = r'[+-]?(?:\d{1,6}(?:\.\d{1,12})?|\.\d{1,12})'
_VECTOR = rf'{_NUMBER}(?:\s*[,，、]\s*{_NUMBER})*'
_NAME = r'(?:[A-Za-z][A-Za-z0-9_-]{0,19}|[甲乙丙丁戊己庚辛壬癸])'
_LABELS = re.compile(rf'(?:(?:同一组)?(?P<count>\d+|[一二三四五六七八九十两])个(?:教学)?样本\s*[,，:：]\s*)?'
                     rf'(?:真实标签|标签|labels)\s*(?:依次)?(?:是|为|[:：=])\s*(?P<labels>{_VECTOR})', re.I)
_MODEL = re.compile(rf'(?:模型|model\s+)\s*({_NAME})\s*(?:给阳性的概率|给的|的阳性概率|的预测概率)?'
                    rf'(?:是|为|[:：=])\s*({_VECTOR})', re.I)
_SCORES = re.compile(rf'({_NAME})\s*[、和与]\s*({_NAME})\s*的\s*Brier\s*分数分别(?:是|为)\s*'
                     rf'({_NUMBER})\s*(?:和|与|、)\s*({_NUMBER})', re.I)
_REPORT_QUESTIONS = re.compile(
    r'(?:只根据这(?:些|[一二三四五六七八九十两\d]+个)样本\s*[,，]\s*)?哪个(?:模型)?总体概率误差较低|'
    r'按各自预测概率分组看\s*[,，]\s*哪个(?:模型)?更符合校准|能据此决定以后用哪个模型吗|'
    r'(?:请)?(?:计算|比较|核对)(?:各模型|这些模型|两个模型)?(?:的)?\s*Brier\s*'
    r'(?:分数|得分)(?:和|与|及)(?:按预测概率分组的)?(?:分组)?校准(?:误差|情况)?', re.I)


def compare_supplied_probabilities(query):
    if 'brier' not in query.casefold():
        return None
    clauses = [part.strip() for part in re.split(r'[。；;？?!！\n]+|[,，](?=\s*(?:模型|model\s+))', query,
                                                  flags=re.I) if part.strip()]
    first = _LABELS.fullmatch(clauses[0]) if clauses else None
    if not first:
        return None
    labels = [Fraction(v) for v in re.split('[,，、]', first['labels'])]
    models, reported, questions = {}, {}, []
    for clause in clauses[1:]:
        if row := _MODEL.fullmatch(clause):
            name, vector = row.groups()
            if name in models:
                return None
            models[name] = [Fraction(v) for v in re.split('[,，、]', vector)]
        elif scores := _SCORES.fullmatch(clause):
            left, right, x, y = scores.groups()
            if reported or left == right:
                return None
            reported = {left: x, right: y}
        elif _REPORT_QUESTIONS.fullmatch(clause):
            questions.append(clause)
        else:
            return None  # Other questions or operations need normal whole-request admission.
    if len(models) < 2 or not questions or reported.keys() - models.keys():
        return None
    count = first['count']
    if count and not count.isdecimal():
        count = '一二三四五六七八九十'.index(count.replace('两', '二')) + 1
    if (count and int(count) != len(labels) or any(label not in (0, 1) for label in labels)
            or any(len(values) != len(labels) or any(v < 0 or v > 1 for v in values) for values in models.values())):
        return {'status': 'partial', 'content': '请核对样本数、0/1 标签和各模型的阳性概率。每个标签需要一项同序概率，范围为 0 到 1。'}

    lines = [f'按你给出的 {len(labels)} 个样本复算，并按每个不同的预测概率分组。']
    briers, gaps = {}, {}
    for name, values in models.items():
        groups = defaultdict(list)
        for probability, label in zip(values, labels):
            groups[probability].append(label)
        brier = sum((p - y) ** 2 for p, y in zip(values, labels)) / len(labels)
        gap = sum(abs(p * len(ys) - sum(ys)) for p, ys in groups.items()) / len(labels)
        briers[name], gaps[name] = brier, gap
        lines.append(f'模型{name}的 Brier 分数为 {float(brier):.6g}，按组样本数加权的绝对校准误差为 {float(gap):.6g}。')
        if name in reported:
            stated = reported[name]
            precision = len(stated.partition('.')[2])
            if abs(Fraction(stated) - brier) > Fraction(1, 2 * 10 ** precision):
                lines.append(f'模型{name}的给定分数 {stated} 与复算值不一致。')
        for p, ys in sorted(groups.items()):
            positives = int(sum(ys))
            observed = Fraction(positives, len(ys))
            direction = '与样本阳性率一致' if p == observed else '高于样本阳性率' if p > observed else '低于样本阳性率'
            lines.append(f'- 阳性概率 {float(p):.6g}，实际阳性 {positives}/{len(ys)}'
                         f'（{float(observed):.6g}），预测{direction}。')
    for metric, values in [('Brier 分数', briers), ('分组校准误差', gaps)]:
        best = min(values.values())
        winners = [name for name in models if values[name] == best]
        if len(winners) == len(models):
            lines.append(f'各模型的{metric}相同。')
        else:
            lines.append(f'在这些样本上，模型{"、".join(winners)}的{metric}最低。')
    lines.append('选择以后使用的模型还需在代表目标用途的独立带标签样本上验证，并检查各组样本量和不确定性。')
    return {'status': 'completed', 'content': '\n\n'.join(lines), 'supplied_calibration': True}
