"""Explicit PDB request syntax only; no identity, source or execution authority."""
import re


_LABEL = re.compile(r"(?<![A-Za-z0-9_-])PDB(?:\s+ID)?(?:\s*[:：=]\s*|\s+(?=[0-9]))", re.I)
# Recognize compact labels only to reject qualified input, never as new syntax.
_COMPACT_LABEL = re.compile(r"(?<![A-Za-z0-9_-])PDB(?:\s+ID)?(?=[0-9])", re.I)
_VALUE = re.compile(r"([0-9][A-Za-z0-9]{3})(?=$|[\s、，,;；。)）\]}]|[.!！?？](?:\s|$))")
_QUALIFIED = re.compile(r"不(?:要|是|查)|勿|别|而非|而是|排除|改(?:为|用)|换成|\b(?:not|except|exclude|instead|without)\b", re.I)
# Exempt only a complete negative docking instruction, not arbitrary negation.
# A following lookup needs its own clause and affirmative operation; shared
# negation such as "do not dock or search" must still fail closed.
_DOCKING_NEGATION = re.compile(
    r"(?:(?:不要|勿|别)\s*(?:进行|执行)?\s*对接"
    r"|\b(?:not|without)\s+(?:(?:perform(?:ing)?|run(?:ning)?|do(?:ing)?)\s+)?dock(?:ing)?\b)"
    r"\s*(?=$|[,，;；。.!！?？]\s*(?:$|(?:查找|查询|搜索|(?:find|search|lookup)\b)))", re.I,
)


def normalize_pdb_id(value):
    if type(value) is not str or re.fullmatch(r"[0-9][A-Za-z0-9]{3}", value.strip()) is None:
        raise ValueError("invalid_pdb_constraint")
    return value.strip().upper()


def has_pdb_constraint(query):
    return type(query) is str and _LABEL.search(query) is not None


def target_text_without_pdb(query):
    """Remove only the already validated constraint and its clause separators."""
    requested_pdb_id(query)
    if type(query) is not str or not has_pdb_constraint(query):
        return query
    label = _LABEL.search(query)
    value = _VALUE.match(query, label.end())
    return query[:label.start()].rstrip(" ,，;；") + " " + query[value.end():].lstrip(" ,，;；")


def requested_pdb_id(query):
    if type(query) is dict:
        if "pdb_id" not in query:
            return None
        targets = [query[k] for k in ("gene_symbol", "target_gene", "uniprot_id", "target_name", "protein_name")
                   if query.get(k)]
        if len(targets) != 1 or type(targets[0]) is not str:
            raise ValueError("ambiguous_pdb_target")
        return normalize_pdb_id(query["pdb_id"])
    if type(query) in (list, tuple):
        if any(requested_pdb_id(item) is not None for item in query):
            raise ValueError("ambiguous_pdb_constraint")
        return None
    if type(query) is not str:
        return None
    labels = list(_LABEL.finditer(query))
    if not labels:
        if _COMPACT_LABEL.search(query) and _QUALIFIED.search(query):
            raise ValueError("ambiguous_pdb_constraint")
        return None
    if len(labels) != 1 or _COMPACT_LABEL.search(query) or any(
        _DOCKING_NEGATION.match(query, qualifier.start()) is None
        for qualifier in _QUALIFIED.finditer(query)
    ):
        raise ValueError("ambiguous_pdb_constraint")
    value = _VALUE.match(query, labels[0].end())
    if value is None:
        raise ValueError("invalid_pdb_constraint")
    if re.match(r"\s*(?:(?:[,，、;；/]|以及|或者|和|及|与|或|\band\b|\bor\b)\s*)+[0-9][A-Za-z0-9]{3}(?![A-Za-z0-9_-])",
                query[value.end():], re.I):
        raise ValueError("ambiguous_pdb_constraint")
    return normalize_pdb_id(value[1])
