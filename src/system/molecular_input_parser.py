"""Shared whole-structure parsing for scientific tools and optimization seeds."""
from dataclasses import dataclass
import re
from typing import Any


_MARKER = re.compile(r'(?<![A-Za-z0-9_])SMILES\s*(?:[:：=]|是|为)[ \t]*', re.I)
LABELED_ACTIVITY_COMPARISON = re.compile(
    r'\s*(?:请\s*)?(?:比较|对比)\s*SMILES\s*[:：=]\s*'
    r'(?P<first>[^\s\u4e00-\u9fff，。；！？]+)\s*(?:和|与)\s*'
    r'(?P<second>[^\s\u4e00-\u9fff，。；！？]+)\s*对\s*'
    r'(?P<target>丁酰胆碱酯酶|[A-Za-z][A-Za-z0-9_-]*)\s*的\s*(?:抑制)?活性\s*[。.!！]?\s*',
    re.I,
)
_FIELD_END = re.compile(r'[\r\n；。;]')
_PDB_REFERENCE = re.compile(
    r'(?:\bPDB\s*(?:ID\s*[:：]?\s*)?|\bRCSB\b[^，。；\r\n]{0,80}?结构\s*)'
    r'(?P<id>[0-9][A-Za-z0-9]{3})(?![A-Za-z0-9])', re.I)
_INVALID = 'SMILES 无效或缺失；请提供完整结构，使用换行或分号分隔，不会提取片段替代。'
_INLINE_PROSE_WRAPPERS = (('(', ')'), ('（', '）'))
_PROSE_WORDS = frozenset({
    'please', 'for', 'and', 'the', 'of', 'in', 'on', 'with', 'from', 'this', 'that',
    'smiles', 'qed', 'logp', 'tpsa', 'hbd', 'hba', 'admet', 'pic50', 'ic50',
})


def expand_labeled_comparison(text):
    """Make the second explicit field visible without changing either value."""
    return re.sub(
        r'^(\s*(?:请\s*)?(?:比较|对比)\s*SMILES\s*[:：=]\s*'
        r'[^\s\u4e00-\u9fff，。；！？]+\s*(?:和|与)\s*)'
        r'([^\s\u4e00-\u9fff，。；！？]+)(?=\s*的)',
        r'\1SMILES: \2', text, count=1, flags=re.I)


class MolecularInputUnavailable(ValueError):
    """Validation could not run; not a diagnosis of the supplied structure."""


class MolecularInputMissing(ValueError):
    """Nonempty supported text contains no molecular candidates."""


class MolecularInputInvalid(ValueError):
    """A complete candidate failed validation, without exposing its content."""

    def __init__(self, position, count):
        super().__init__(_INVALID)
        self.position = position
        self.count = count


@dataclass(frozen=True)
class ParsedMolecularInput:
    """One validated input and the RDKit results produced at that boundary."""

    smiles: str
    molecule: Any
    canonical_smiles: str


def _is_prose_word(value, tool):
    # SMILES is case-sensitive: ON/IN/OF are structures, on/in/of are prose.
    return (value.casefold() in tool.exclude_words | _PROSE_WORDS
            and not (value.isupper() and tool._is_plausible_smiles_lexeme(value)))


def _looks_like_structure(value, tool):
    if _is_prose_word(value, tool):
        return False
    if not re.search(r'[A-Za-z]', value):
        return False
    # Keep an entire malformed candidate, not just its valid prefix. A capital
    # first letter alone (Please/Calculate) is not evidence of molecular input.
    return (tool._is_plausible_smiles_lexeme(value)
            or bool(re.match(
                r'^(?:[BCNOPSFI]{2}|(?:Cl|Br)(?=[BCNOPSFIbcnops0-9()[\]])'
                r'|[BCNOPSFIbcnops][0-9()[\]=#@+\-]|\[)', value)))


def _unwrap_inline_prose_structure(value, tool):
    """Remove one prose-only wrapper around an inline SMILES candidate.

    A user commonly writes ``乙醇(CCO)`` or ``乙醇（CCO）``.  The wrappers
    are not part of the SMILES field; only remove them when the complete
    inner value already looks like a plausible structure.  This deliberately
    leaves malformed suffixes such as ``(CCO)broken`` untouched so the whole
    input contract still rejects them instead of repairing a fragment.
    """
    candidate = _unquote(value)
    for opening, closing in _INLINE_PROSE_WRAPPERS:
        if (candidate.startswith(opening) and candidate.endswith(closing)
                and len(candidate) > len(opening) + len(closing)):
            inner = candidate[len(opening):-len(closing)]
            if _looks_like_structure(inner, tool):
                return inner
    return candidate


def parse_molecular_smiles(text, tool, *, prose_pattern=None):
    """Return every validated input SMILES as text for legacy callers."""
    return [
        parsed.smiles
        for parsed in parse_molecular_inputs(
            text, tool, prose_pattern=prose_pattern
        )
    ]


def parse_molecular_inputs(text, tool, *, prose_pattern=None):
    """Return every full input SMILES or reject the batch; never repair/drop one.

    Labels establish authoritative fields. Otherwise conservative whole tokens
    support legacy analysis prompts and newline-joined evidence bindings.
    Whitespace/name/CXSMILES extensions within a field are deliberately unsupported.
    An optional domain phrase pattern applies only to prose, not explicit fields.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 65536:
        raise ValueError(_INVALID)
    text = expand_labeled_comparison(text)
    # A single-letter candidate label before an explicit field is not an atom.
    text = re.sub(r'(?<![A-Za-z0-9_])[A-Z](?=\s*的\s*SMILES\s*(?:[:：=]|是|为))', '', text)
    markers = list(_MARKER.finditer(text))
    if markers:
        values = _bare_values(text[:markers[0].start()], tool, prose_pattern)
        for index, marker in enumerate(markers):
            end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
            field_text = text[marker.end():end]
            if index + 1 < len(markers):
                field_text = re.sub(r'\s+(?:和|与|and)\s*$', '', field_text, flags=re.I)
            # A full-width parenthesis can wrap the labelled field in Chinese
            # prose; SMILES itself uses ASCII parentheses, so this delimiter
            # cannot terminate an internal branch.
            if text[:marker.start()].rstrip().endswith('（') and '）' in field_text:
                value, prose = field_text.split('）', 1)
                if prose and re.match(r'[A-Za-z0-9_]', prose):
                    raise ValueError(_INVALID)
                values.append(_labeled_field_value(value.strip(), tool))
                values.extend(_bare_values(prose, tool, prose_pattern))
                continue
            # A Chinese comma separates the labeled field from the following
            # clause; inspect that clause for further structures as usual.
            fields = re.split(r'[\r\n；。，;]|\.[ \t]+(?=[\u4e00-\u9fff])',
                              field_text, maxsplit=1)
            values.append(_labeled_field_value(fields[0].strip(), tool))
            if len(fields) > 1:
                values.extend(_bare_values(fields[1], tool, prose_pattern))
    else:
        values = _bare_values(text, tool, prose_pattern)
    if not values:
        raise MolecularInputMissing(_INVALID)
    if len(values) > 100:
        raise ValueError(_INVALID)
    # No heuristic fallback: these tools must have real structure validation.
    try:
        from rdkit import Chem, rdBase
    except ImportError:
        raise MolecularInputUnavailable('RDKit 不可用，无法验证 SMILES。') from None
    try:
        params = Chem.SmilesParserParams()
        params.parseName = False
        params.allowCXSMILES = False
    except Exception:
        raise MolecularInputUnavailable('SMILES 校验暂不可用。') from None
    parsed_values = []
    for position, value in enumerate(values, 1):
        # SMILES is printable ASCII. Some RDKit builds accept a valid prefix
        # before a Unicode suffix, so RDKit success alone cannot prove complete
        # consumption of an authoritative field. Never calculate that prefix.
        if not value or len(value) > 8192 or re.search(r'[^\x21-\x7e]', value):
            raise MolecularInputInvalid(position, len(values))
        try:
            with rdBase.BlockLogs():
                mol = Chem.MolFromSmiles(value, params)
            valid = mol is not None and bool(mol.GetNumAtoms())
        except Exception:
            raise MolecularInputUnavailable('SMILES 校验暂不可用。') from None
        if not valid:
            raise MolecularInputInvalid(position, len(values))
        try:
            canonical = Chem.MolToSmiles(mol, canonical=True)
        except Exception:
            raise MolecularInputUnavailable('SMILES 校验暂不可用。') from None
        if not canonical:
            raise MolecularInputInvalid(position, len(values))
        parsed_values.append(
            ParsedMolecularInput(
                smiles=value,
                molecule=mol,
                canonical_smiles=canonical,
            )
        )
    return tuple(parsed_values)


def _labeled_field_value(value, tool):
    """Read a labeled SMILES followed by a Chinese request clause.

    Users commonly write ``SMILES: CCO 的 ADMET 风险``.  The Chinese ``的``
    clause is prose outside the labeled structure field, but arbitrary ASCII
    suffixes such as ``SMILES: CCO invalid_suffix`` must remain strict and be
    rejected by the full-field validator below.
    """
    candidate = _unquote(value.strip())
    separator = re.search(r'\s+(?:(?:的|是否为|是否符合)(?=\s|[\u4e00-\u9fff]|Lipinski\b)|做(?=\s*ADMET\s*预测(?:\s|$)))', candidate, re.I)
    if separator:
        prefix = candidate[:separator.start()].strip()
        suffix = candidate[separator.end():].strip()
        if suffix and _looks_like_structure(prefix, tool):
            return prefix
    return candidate


def _bare_values(text, tool, prose_pattern=None):
    values = []
    # Only suppress identifiers explicitly associated with a PDB source;
    # labeled SMILES fields are parsed separately and remain authoritative.
    text = _PDB_REFERENCE.sub(lambda match: match.group()[:match.start('id') - match.start()]
                              + ' ' * len(match.group('id')), text)
    for fragment in _FIELD_END.split(text):
        fragment = fragment.strip()
        if not fragment:
            continue
        first = fragment.split()[0]
        if _is_prose_word(fragment, tool):
            continue
        phrases = list(prose_pattern.finditer(fragment)) if prose_pattern else []
        for phrase in phrases:
            # Chinese phrase endings split tokens; inspect the raw boundary so
            # an attached malformed suffix cannot disappear during tokenization.
            suffix = re.match(r'[^\s\u4e00-\u9fff，！？：、]+', fragment[phrase.end():])
            if suffix and suffix.group() not in ('.', '!', '?'):
                raise ValueError(_INVALID)
        if phrases and phrases[0].start() == 0:
            # A domain phrase can lead a supported request, but must not hide
            # arbitrary suffixes or borrow context across authoritative fields.
            rest = fragment[phrases[0].end():].strip()
            if not rest or rest in ('.', '!', '?'):
                continue
            if re.match(r'^for[ \t]+', rest, re.I):
                values.append(_unquote(re.sub(r'^for[ \t]+', '', rest, flags=re.I)))
            else:
                values.append(fragment)
            continue
        structure_leading = _looks_like_structure(_unquote(first), tool)
        # Standalone batch fields are authoritative even with illegal suffixes.
        # A structure-leading ASCII field with spaces is not a molecule name.
        if (not re.search(r'[\u4e00-\u9fff]', fragment)
                and (len(fragment.split()) == 1
                     or structure_leading)):
            values.append(_unquote(fragment))
            continue
        tokens = re.finditer(r'[^\s\u4e00-\u9fff，！？：、]+', fragment)
        for token in tokens:
            if any(phrase.start() <= token.start() < phrase.end()
                   and (token.end() <= phrase.end()
                        or fragment[phrase.end():token.end()] in ('.', '!', '?'))
                   for phrase in phrases):
                continue
            if any(token.start() < phrase.end() and token.end() > phrase.start() for phrase in phrases):
                raise ValueError(_INVALID)
            value = _unwrap_inline_prose_structure(token.group(), tool)
            if _looks_like_structure(value, tool):
                values.append(value)
    return values


def _unquote(value):
    if value.startswith(('"', "'", '`')):
        if len(value) < 2 or value[-1] != value[0]:
            raise ValueError('SMILES 引号未闭合；请提供完整结构。')
        return value[1:-1]
    return value
