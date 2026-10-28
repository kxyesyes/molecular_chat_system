"""Lossless public scientific JSON except explicit privacy redaction.

Not evidence acceptance: callers must supply an already verified result. Unlike
the legacy prose sanitizer this boundary never truncates lists, text or depth,
and does not interpret stereochemical slashes, units or public URLs as paths.
"""
import re

from src.system.redaction import contains_secret_material
from src.agent.scientific_session.decision_bounds import validate_json


MAX_DETAIL_BYTES = 4 * 1024 * 1024
MAX_DETAIL_NODES = 65536
# The sequence's observation objects are two levels below the snapshot root.
MAX_DETAIL_DEPTH = 34
_REDACTED = '[redacted]'
_URL = re.compile(r'\bhttps?://[^\s<>"\']+', re.I)
_FILE_URL = re.compile(r'\bfile:', re.I)
_PRIVATE_KEY = re.compile(r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----')
_WINDOWS_PATH = re.compile(r'(?<![A-Za-z0-9])(?:[A-Z]:[\\/]|\\\\[^\\\s]+\\)', re.I)
_POSIX_PATH = re.compile(r'(?<![\w:/])/(?:[^\s/;,"\']+/)+[^\s;,"\']*')
_ROOT_FILE = re.compile(r'(?<![\w:/])/[^\s/;,"\']*\.[A-Za-z0-9][^\s/;,"\']*')
_PATH_KEYS = frozenset({'path', 'file', 'filename', 'file_path', 'directory', 'model_path',
                        'weights_path', 'cache_dir', 'output_dir', 'input_path', 'output_path'})
_TOKEN_BOUNDARIES = frozenset(' \t\r\n|`"\'：，；')


def _molecular_token(text, position):
    """Disambiguate a slash inside a bounded, complete molecular token.

    This is privacy classification only, never chemical result validation or
    evidence acceptance. Invalid paths cannot bypass it by using a SMILES key.
    """
    start, end = position, position
    while start > 0 and text[start - 1] not in _TOKEN_BOUNDARIES and position - start <= 8192:
        start -= 1
    while end < len(text) and text[end] not in _TOKEN_BOUNDARIES and end - start <= 8192:
        end += 1
    token = text[start:end]
    if not token or len(token) > 8192 or token.startswith('/'):
        return False
    from rdkit import Chem, rdBase
    params = Chem.SmilesParserParams()
    params.parseName = False
    params.allowCXSMILES = False
    with rdBase.BlockLogs():
        if Chem.MolFromSmiles(token, params) is not None:
            return True
        # Markdown emphasis is presentation, not part of the molecular token.
        for marker in ('**', '__', '*', '_'):
            if token.startswith(marker) and token.endswith(marker):
                core = token[len(marker):-len(marker)]
                if core and not core.startswith('/') and Chem.MolFromSmiles(core, params) is not None:
                    return True
        return False


def public_scientific_data(value, *, capacity_reason=None):
    """Return a detached JSON value and whether private content was redacted."""
    validate_json(value, max_bytes=MAX_DETAIL_BYTES, max_nodes=MAX_DETAIL_NODES,
                  max_depth=MAX_DETAIL_DEPTH, compact=True, reason='result_details_invalid',
                  capacity_reason=capacity_reason)
    changed = False

    def text(value, key):
        nonlocal changed
        without_urls = _URL.sub(' ', value)
        if (contains_secret_material(value) or _PRIVATE_KEY.search(value) or _FILE_URL.search(value)
                or _WINDOWS_PATH.search(without_urls)
                or any(not _molecular_token(without_urls, match.start())
                       for pattern in (_POSIX_PATH, _ROOT_FILE)
                       for match in pattern.finditer(without_urls))
                or (key in _PATH_KEYS and value.startswith('/'))):
            changed = True
            return _REDACTED
        return value

    def visit(item, key=''):
        nonlocal changed
        if type(item) is str:
            return text(item, key)
        if type(item) is list:
            return [visit(child, key) for child in item]
        if type(item) is dict:
            output = {}
            for name, child in item.items():
                normalized = name.casefold().replace('-', '_')
                if (contains_secret_material({name: None})
                        or text(name, '') != name):
                    changed = True
                    continue
                output[name] = visit(child, normalized)
            return output
        return item

    result = visit(value)
    # Redaction markers can be longer than the removed short private text.
    validate_json(result, max_bytes=MAX_DETAIL_BYTES, max_nodes=MAX_DETAIL_NODES,
                  max_depth=MAX_DETAIL_DEPTH, compact=True, reason='result_details_invalid',
                  capacity_reason=capacity_reason)
    return result, changed
