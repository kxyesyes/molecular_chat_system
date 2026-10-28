"""Non-authorizing text view shared by comparison admission and input parsing."""
import re


# Display objects are existing report material, not permission to calculate a
# new endpoint. Match complete clauses; never discard these words globally.
_DISPLAY_ITEM_ZH = (
    r'(?:(?:所有|全部)\s*)?(?:原始(?:预测)?(?:结果|数据)|原始预测|'
    r'一致性检查(?:结果)?|排除原因|预测分歧)'
)
_DISPLAY_ITEM_EN = (
    r'(?:(?:all|the)\s+)?(?:(?:raw|original)\s+(?:predictions?|results?|data)|'
    r'consistency\s+checks?(?:\s+results)?|exclusion\s+reasons|prediction\s+disagreements)'
)
_PRESENTATION_CLAUSE = re.compile(
    r'(?:按可用结果给出比较|如果预测存在分歧请明确指出|'
    r'按(?:可用且通过检查的证据|活性证据|预测\s*pIC50\s*降序)排序|'
    rf'(?:请\s*)?(?:保留|展示|显示|列出|提供)\s*{_DISPLAY_ITEM_ZH}'
    rf'(?:\s*(?:以及|和|与|及|、)\s*{_DISPLAY_ITEM_ZH})*|'
    rf'(?:please\s+)?(?:preserve|keep|show|display|include)\s+{_DISPLAY_ITEM_EN}'
    rf'(?:\s+and\s+{_DISPLAY_ITEM_EN})*)', re.I,
)


def without_comparison_presentation(text):
    """Return a parsing view, never a replacement for the original request."""
    # A dot within a disconnected SMILES is not a prose separator. Ambiguous
    # unspaced ASCII prose remains intact and fails closed.
    parts = re.split(r'([，,；;。!?！？\n]|\.(?=\s|$))', text)
    for index in range(0, len(parts), 2):
        if _PRESENTATION_CLAUSE.fullmatch(parts[index].strip()):
            parts[index] = ''
            # Retain the preceding separator, so unknown neighbours cannot
            # merge. Drop this clause's terminator to preserve an English
            # for-object sentence ending in one dot, not an invented '..'.
            if index + 1 < len(parts):
                parts[index + 1] = ''
    return ''.join(parts)
