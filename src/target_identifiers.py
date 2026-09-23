"""Dependency-free explicit target identifiers, not intent or capability claims.

PDE aliases describe the supported activity-family vocabulary. Recognizing one
does not establish that structures, assay data or trained weights are available.
"""
import re
from types import MappingProxyType


PDE_SUBTYPES = (
    '1A', '1B', '1C', '2A', '3A', '3B', '4A', '4B', '4C', '4D',
    '5A', '6A', '6B', '6C', '6D', '6G', '6H', '7A', '7B', '8A',
    '8B', '9A', '10A', '11A',
)
PDE_ALIASES = frozenset({'pde', 'pde-family'} | {
    f'pde{subtype.lower()}' for subtype in PDE_SUBTYPES
} | {f'pde{number}' for number in range(1, 12)})
BUCHE_ALIASES = frozenset({'buche', 'bche', 'buche-family', '丁酰胆碱酯酶'})
TARGET_ALIASES = MappingProxyType({
    **{alias: alias.upper() for alias in PDE_ALIASES},
    'pde-family': 'PDE',
    **{alias: 'BCHE' for alias in BUCHE_ALIASES},
    **{name.lower(): name for name in (
        'EGFR', 'BACE1', 'KRAS', 'BRAF', 'JAK2', 'ALK', 'MET', 'CDK2', 'KDR',
    )},
})
# ASCII token boundaries permit adjacent Chinese prose, but never embedded IDs,
# suffixes or hyphen/underscore compounds. Exact identifier APIs stay stricter.
TARGET_PATTERN = re.compile(
    r'(?<![A-Za-z0-9_-])('
    + '|'.join(re.escape(alias) for alias in sorted(TARGET_ALIASES, key=len, reverse=True))
    + r')(?![A-Za-z0-9_-])', re.I,
)


def canonical_target_identifier(value: str) -> str | None:
    """Normalize one complete identifier; unknown values remain unknown."""
    return TARGET_ALIASES.get(value.strip().casefold()) if isinstance(value, str) else None


def target_mentions(text: str) -> tuple[str, ...]:
    """Return all recognized identities in mention order, without choosing one."""
    return tuple(dict.fromkeys(
        TARGET_ALIASES[match[1].casefold()] for match in TARGET_PATTERN.finditer(text)
    ))
