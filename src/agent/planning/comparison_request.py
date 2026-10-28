"""A non-executable obligation view for the bounded comparison workflow.

Molecular operands and complete display clauses are not metric lists. Both Web
entry points use this view; neither can fall back to activity-only execution
when an unreviewed comparison obligation remains.
"""
from dataclasses import dataclass
import re

from src.agent.contracts.comparison_presentation import without_comparison_presentation
from src.agent.planning.request_parsing import positive_intent_text
from src.agent.tools.base_tool import BaseMolecularTool
from src.system.molecular_input_parser import parse_molecular_inputs
from src.target_identifiers import TARGET_PATTERN


# Compatibility labels from the existing named-comparison workflow, not a name
# resolver. Structures remain the only dispatched identities. Unrecognized
# labels require an explicit SMILES list; arbitrary prose is never a label.
_COMPOUND_LABELS = ('阿司匹林', '对乙酰氨基酚', '咖啡因')
COMPARISON_PROPERTY_TERMS = re.compile(
    r'(?:分子)?(?:理化)?性质|分子量|'
    r'(?<![A-Za-z0-9_])(?:molecular\s+propert(?:y|ies)|propert(?:y|ies)|'
    r'molecular\s+weight|MW|logP|TPSA|QED|HBA|HBD)(?![A-Za-z0-9_])', re.I,
)

@dataclass(frozen=True)
class ComparisonRequest:
    obligation_text: str
    supported: bool


def _whole_structure(text):
    """Validate exactly one field, never extract a structure from request prose."""
    value = text.strip()
    # The shared request parser accepts multiple fields and surrounding prose.
    # Enforce its single-field precondition before using its RDKit validation.
    if not value or re.search(r'[\s；;。]', value):
        return False
    try:
        parsed = parse_molecular_inputs('SMILES: ' + value, BaseMolecularTool('comparison_input', ''))
        return len(parsed) == 1
    except ValueError:
        return False


def _whole_subject_list(text):
    # Finding structures in prose is not proof that the prose is only a list.
    # Split once and validate each complete field. Building repeated regex
    # alternatives from candidates causes exponential backtracking on duplicates.
    for member in re.split(r'和|与|及|、|，|,|\band\b', text, flags=re.I):
        value = member.strip()
        for label in _COMPOUND_LABELS:
            if (value.startswith(label) and len(value) > len(label)
                    and value[len(label)].isspace()):
                value = value[len(label):].lstrip()
                break
        if not _whole_structure(value):
            return False
    return True


def comparison_request(query):
    text = without_comparison_presentation(positive_intent_text(query))
    # Chinese subject names may accompany structures. Only a grammatical
    # subject before 的 is separate from obligations; later 的 stays visible.
    subject = re.match(r'^\s*请?\s*(?:比较|对比)([^的。；;]+)的', text)
    if subject and _whole_subject_list(subject[1]):
        text = text[subject.end():]

    # English for-objects contain an explicit list, not an arbitrary trailing
    # instruction. Validate each complete token and leave unknown words visible.
    def english_objects(match):
        body = match[1].rstrip()
        terminator = '.' if body.endswith('.') else ''
        candidate = body[:-1] if terminator else body
        tokens = re.split(r'\s+(?:and|or)\s+|\s*,\s*', candidate.strip(), flags=re.I)
        if not tokens or any(not _whole_structure(token) for token in tokens):
            return match[0]
        return ' ' + terminator

    text = re.sub(r'\bfor\s+([^;；。!?\n]+)', english_objects, text, flags=re.I)
    # A labelled field is authoritative in its entirety. Do not trim dots or
    # suffixes here, and do not let a valid first structure hide extra text.
    text = re.sub(r'(?<![A-Za-z0-9_])SMILES\s*[:：=][ \t]*([^\r\n；。;]+)',
                  lambda match: ' ' if _whole_structure(match[1]) else match[0],
                  text, flags=re.I)
    text = TARGET_PATTERN.sub(' ', text)
    # English words require ASCII token boundaries (Chinese may be adjacent);
    # Chinese capabilities are
    # removed separately so FooMetric活性 cannot hide inside a known substring.
    reviewed = (
        r'(?<![A-Za-z0-9_])(?:please|compare|and|rank|ADMET|ADME|activity|'
        r'potency|pIC50|'
        r'predict|prediction|calculate|including|include|with)(?![A-Za-z0-9_])|'
        r'活性预测|活性|完整|针对|预测|计算|比较|对比|的|和|与|以及|及'
    )
    remaining = re.sub(reviewed, ' ', COMPARISON_PROPERTY_TERMS.sub(' ', text), flags=re.I)
    supported = not re.search(r'[^\s，,；;。.!?！？、]', remaining)
    return ComparisonRequest(text, supported)
