"""Security contracts for serialized RG-MPNN graph datasets."""

from __future__ import annotations

import pickle

import pytest
import torch
from torch_geometric.data import Data


def _write_marker(path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("executed")


class _UnsafePayload:
    def __init__(self, marker: str):
        self.marker = marker

    def __reduce__(self):
        return (_write_marker, (self.marker,))


def test_processed_dataset_loader_accepts_pyg_data_without_unrestricted_pickle(tmp_path):
    from src.activity.rg_mpnn.molecular_network.mol_dataset._safe_load import (
        load_processed_graph_dataset,
    )

    path = tmp_path / "trusted.pt"
    torch.save((Data(x=torch.tensor([[1.0]])), {}), path)

    data, slices = load_processed_graph_dataset(path)

    assert isinstance(data, Data)
    assert slices == {}


def test_processed_dataset_loader_rejects_unknown_pickle_payload_without_execution(tmp_path):
    from src.activity.rg_mpnn.molecular_network.mol_dataset._safe_load import (
        load_processed_graph_dataset,
    )

    marker = tmp_path / "executed.txt"
    path = tmp_path / "untrusted.pt"
    torch.save((_UnsafePayload(str(marker)), {}), path)

    with pytest.raises((pickle.UnpicklingError, RuntimeError)):
        load_processed_graph_dataset(path)

    assert not marker.exists()
