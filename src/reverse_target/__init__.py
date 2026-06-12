"""Reverse-target module package.

Keep package import lightweight. Heavy builders and predictors import RDKit,
NumPy arrays, and large ChEMBL resources, so callers should import those
classes from their concrete modules only when needed.
"""

__all__: list[str] = []
