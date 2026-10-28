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
