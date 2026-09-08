"""Whole-structure parsing for migrated analysis tools (not molecule generation)."""
import re


_MARKER = re.compile(r'(?<![A-Za-z0-9_])SMILES\s*[:：=][ \t]*', re.I)
_FIELD_END = re.compile(r'[\r\n；。;]')
_INVALID = 'SMILES 无效或缺失；请提供完整结构，使用换行或分号分隔，不会提取片段替代。'
_PROSE_WORDS = frozenset({
    'please', 'for', 'and', 'the', 'of', 'in', 'on', 'with', 'from', 'this', 'that',
    'smiles', 'qed', 'logp', 'tpsa', 'hbd', 'hba', 'admet', 'pic50', 'ic50',
})


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


def parse_molecular_smiles(text, tool):
    """Return every full input SMILES or reject the batch; never repair/drop one.

    Labels establish authoritative fields. Otherwise conservative whole tokens
    support legacy analysis prompts and newline-joined evidence bindings.
    Whitespace/name/CXSMILES extensions within a field are deliberately unsupported.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 65536:
        raise ValueError(_INVALID)
    markers = list(_MARKER.finditer(text))
    if markers:
        values = _bare_values(text[:markers[0].start()], tool)
        for index, marker in enumerate(markers):
            end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
            fields = _FIELD_END.split(text[marker.end():end], maxsplit=1)
            values.append(_unquote(fields[0].strip()))
            if len(fields) > 1:
                values.extend(_bare_values(fields[1], tool))
    else:
        values = _bare_values(text, tool)
    if not values or len(values) > 100:
        raise ValueError(_INVALID)
    # No heuristic fallback: these tools must have real structure validation.
    try:
        from rdkit import Chem, rdBase
    except ImportError:
        raise ValueError('RDKit 不可用，无法验证 SMILES。') from None
    params = Chem.SmilesParserParams()
    params.parseName = False
    params.allowCXSMILES = False
    for value in values:
        if not value or len(value) > 8192 or re.search(r'\s', value):
            raise ValueError(_INVALID)
        with rdBase.BlockLogs():
            mol = Chem.MolFromSmiles(value, params)
        if mol is None or not mol.GetNumAtoms():
            raise ValueError(_INVALID)
    return values


def _bare_values(text, tool):
    values = []
    for fragment in _FIELD_END.split(text):
        fragment = fragment.strip()
        if not fragment:
            continue
        first = fragment.split()[0]
        if _is_prose_word(fragment, tool):
            continue
        structure_leading = _looks_like_structure(_unquote(first), tool)
        # Standalone batch fields are authoritative even with illegal suffixes.
        # A structure-leading ASCII field with spaces is not a molecule name.
        if (not re.search(r'[\u4e00-\u9fff]', fragment)
                and (len(fragment.split()) == 1
                     or structure_leading)):
            values.append(_unquote(fragment))
            continue
        tokens = re.findall(r'[^\s\u4e00-\u9fff，！？：、]+', fragment)
        for token in tokens:
            value = _unquote(token)
            if _looks_like_structure(value, tool):
                values.append(value)
    return values


def _unquote(value):
    if value.startswith(('"', "'", '`')):
        if len(value) < 2 or value[-1] != value[0]:
            raise ValueError('SMILES 引号未闭合；请提供完整结构。')
        return value[1:-1]
    return value
