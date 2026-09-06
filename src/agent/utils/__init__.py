from typing import Any

__all__ = ["SMILESExtractor"]


def __getattr__(name: str) -> Any:
    if name == "SMILESExtractor":
        from .smiles_extractor import SMILESExtractor

        return SMILESExtractor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
