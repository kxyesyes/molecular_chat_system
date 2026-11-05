"""Narrow, whole-request recognition for a property-only design workflow."""
import re


_REQUEST = re.compile(
    r'\s*(?:请\s*)?(?:针对|基于)\s*[A-Za-z][A-Za-z0-9_-]{0,63}\s*'
    r'(?:生成|设计)\s*[1-9][0-9]?\s*个候选分子\s*[,，]\s*'
    r'(?:计算|评估)\s*(?:分子|理化)?性质\s*[,，]?\s*'
    r'(?:并\s*)?(?:根据|按)\s*(?:分子|理化)?性质\s*(?:进行)?(?:排序|排名)\s*[。.!！]?\s*',
    re.I,
)


def property_only_design_request(query: object) -> bool:
    return isinstance(query, str) and _REQUEST.fullmatch(query) is not None


def descriptor_only_design_request(query: object) -> bool:
    return isinstance(query, str) and re.fullmatch(
        r'\s*(?:请\s*)?(?:针对|基于)\s*(?:人源\s*)?[A-Za-z][A-Za-z0-9_-]{0,63}\s*'
        r'(?:生成|设计)\s*[1-9][0-9]?\s*个候选(?:小)?分子\s*[,，]\s*'
        r'(?:(?:给出|列出)\s*SMILES\s*[,，]\s*)?(?:并\s*)?计算(?:每个分子的|各分子的|其)?\s*'
        r'(?:分子量|MW|LogP)(?:\s*(?:和|及|与|、|[,，])\s*(?:分子量|MW|LogP))+'
        r'(?:\s*[,，]\s*(?:并\s*)?预测\s*ADMET)?\s*[。.!！]?'
        r'(?:\s*(?:请\s*)?不要将(?:预测结果当作实验事实|生成结果当作已验证的抑制剂)[。.!！]?)?\s*', query, re.I) is not None
