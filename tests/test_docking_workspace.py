from pathlib import Path

import pytest

from src.docking.cavity_search import detect_cavities


def test_real_apo_candidates_are_ordered_and_have_editable_boxes():
    sample = Path(__file__).resolve().parents[1] / "data/samples/PDE4A_apo_6FG5.pdb"
    pockets = detect_cavities(sample.read_bytes())["cavities"]
    assert 1 <= len(pockets) <= 5
    assert pockets[0]["volume"] >= pockets[-1]["volume"]
    assert all(len(p["center"]) == 3 and all(10 <= s <= 50 for s in p["size"]) for p in pockets)


def test_no_protein_is_reported():
    with pytest.raises(ValueError):
        detect_cavities(b"END\n")
