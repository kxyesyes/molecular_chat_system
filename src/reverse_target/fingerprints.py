"""Fingerprint array representation shared by database builds and queries."""

import numpy as np


def bitvect_to_numpy_array(bitvect) -> np.ndarray:
    """Return one uint8 per bit, including with older RDKit/NumPy bridges."""
    from rdkit import DataStructs

    num_bits = bitvect.GetNumBits()
    arr = np.zeros(num_bits, dtype=np.uint8)
    try:
        DataStructs.ConvertToNumpyArray(bitvect, arr)
        return arr
    except (TypeError, ValueError):
        return np.fromiter(
            (1 if bitvect.GetBit(index) else 0 for index in range(num_bits)),
            dtype=np.uint8,
            count=num_bits,
        )
