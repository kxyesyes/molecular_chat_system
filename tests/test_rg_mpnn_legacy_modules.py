from __future__ import annotations

import importlib

import torch
from torch_geometric.data import Data


def test_sort_tid_maps_sparse_ids_to_contiguous_sorted_ids() -> None:
    module = importlib.import_module("src.activity.rg_mpnn.Utils.sort_tid")

    result = module.sort_tid(torch.tensor([10, 5, 10, 8], dtype=torch.long))

    assert torch.equal(result, torch.tensor([2, 0, 2, 1], dtype=torch.long))


def test_complete_virtual_self_marks_real_virtual_and_self_edges() -> None:
    module = importlib.import_module(
        "src.activity.rg_mpnn.molecular_network.transform.complete"
    )
    transform = module.Complete_Virtual_Self()
    data = Data(
        x=torch.ones((2, 1)),
        edge_index=torch.tensor([[0, 1], [1, 0]], dtype=torch.long),
        edge_attr=torch.tensor(
            [[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]]
        ),
    )

    transformed = transform(data)

    assert transformed.edge_index.shape == (2, 4)
    assert transformed.edge_attr.shape == (4, 6)
    assert torch.equal(
        transformed.edge_attr[[1, 2], :4],
        torch.tensor([[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]]),
    )
    assert torch.equal(transformed.edge_attr[[0, 3], 5], torch.ones(2))
    assert torch.equal(transformed.edge_attr[:, 4], torch.zeros(4))
