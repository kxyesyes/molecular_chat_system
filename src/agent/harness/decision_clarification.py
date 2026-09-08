"""Fixed scientific explanations backed by current input validation, not model prose."""
from src.agent.utils.validators import InputValidator

from .decision_inputs import MOLECULAR_INPUT_TOOLS


GENERIC = '需要补充或确认输入；本次任务尚未完成，请核对原始分子或靶点信息。'
UNAVAILABLE = 'SMILES 校验暂不可用，无法判断当前结构是否有效；本次任务尚未完成，请稍后重试。'
# Clarification is on the control path, not the scientific worker. Avoid expensive
# parsing here; long inputs keep the neutral prompt and existing tool boundaries.
MAX_EXPLANATION_INPUT_BYTES = 256


def scientific_clarification(query, required_tools):
    """Explain only validated molecular obligations; never echo query/question/exception."""
    if not MOLECULAR_INPUT_TOOLS.intersection(required_tools):
        return GENERIC
    if not isinstance(query, str) or len(query.encode('utf-8')) > MAX_EXPLANATION_INPUT_BYTES:
        return GENERIC
    try:
        from rdkit import rdBase
        # RDKit parse diagnostics can echo the entire input to stderr.
        with rdBase.BlockLogs():
            analysis = InputValidator().analyze_molecular_input(query)
    except Exception:
        return UNAVAILABLE
    if not analysis.validation_available or any(
            candidate.validation == 'unavailable' for candidate in analysis.candidates):
        return UNAVAILABLE
    invalid = [candidate for candidate in analysis.candidates if candidate.validation == 'invalid']
    # A rejected Markdown-wrapped token is formatting evidence, not a molecule diagnosis.
    if any('`' not in candidate.value for candidate in invalid):
        return ('输入中的 SMILES 无效，未通过 RDKit 结构校验；请检查完整结构后重新提交。'
                '本次任务尚未完成，不会将结构片段或替代分子作为原始输入的计算结果。')
    if invalid:
        return ('输入格式需要确认；请提供不含 Markdown 标记的完整 SMILES。'
                '本次任务尚未完成，不能从可解析的片段推断完整结构有效。')
    if not analysis.candidates:
        return '未识别到可校验的分子结构；请提供完整的 SMILES。本次任务尚未完成。'
    return GENERIC
