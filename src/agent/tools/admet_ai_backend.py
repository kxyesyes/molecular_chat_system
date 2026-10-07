"""Compatibility module for the historical ADMET backend import path."""

import sys

from src.admet import backend as _domain_backend

sys.modules[__name__] = _domain_backend
