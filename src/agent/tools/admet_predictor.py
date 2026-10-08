"""Compatibility module for the historical Agent ADMET import path.

The implementation now lives in :mod:`src.admet.predictor`.  Replacing this
module entry with the domain module keeps legacy imports and monkeypatch seams
bound to the same implementation instead of maintaining a second predictor.
"""

import sys

from src.admet import predictor as _domain_predictor

sys.modules[__name__] = _domain_predictor
