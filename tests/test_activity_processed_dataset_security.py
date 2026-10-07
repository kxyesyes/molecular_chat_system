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


@pytest.mark.parametrize("unsafe", [False, True])
def test_legacy_safe_globals_api_remains_restricted_and_restores_allowlist(tmp_path, monkeypatch, unsafe):
    from src.activity.rg_mpnn.molecular_network.mol_dataset._safe_load import (
        load_processed_graph_dataset,
    )

    # PyTorch 2.4 has add/get/clear_safe_globals, but no context manager.
    monkeypatch.delattr(torch.serialization, "safe_globals", raising=False)
    before = list(torch.serialization.get_safe_globals())
    marker = tmp_path / "executed.txt"
    path = tmp_path / "legacy.pt"
    torch.save((_UnsafePayload(str(marker)) if unsafe else Data(x=torch.ones(1)), {}), path)
    try:
        if unsafe:
            with pytest.raises((pickle.UnpicklingError, RuntimeError)):
                load_processed_graph_dataset(path)
        else:
            data, slices = load_processed_graph_dataset(path)
            assert isinstance(data, Data) and slices == {}
        assert not marker.exists()
        assert set(torch.serialization.get_safe_globals()) == set(before)
    finally:
        torch.serialization.clear_safe_globals()
        torch.serialization.add_safe_globals(before)


def test_no_allowlist_api_fails_closed_before_loading(tmp_path, monkeypatch):
    from src.activity.rg_mpnn.molecular_network.mol_dataset._safe_load import (
        load_processed_graph_dataset,
    )

    monkeypatch.delattr(torch.serialization, "safe_globals", raising=False)
    monkeypatch.delattr(torch.serialization, "add_safe_globals")
    with pytest.raises(RuntimeError, match="safe globals are unavailable"):
        load_processed_graph_dataset(tmp_path / "not-opened.pt")
