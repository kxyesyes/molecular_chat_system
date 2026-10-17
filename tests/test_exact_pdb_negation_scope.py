"""Offline exact-PDB syntax contracts; identifiers are not scientific evidence."""

import pytest

from src.target_search.exact_pdb import has_pdb_constraint, requested_pdb_id, target_text_without_pdb


@pytest.mark.parametrize("query", [
    "查找 BCHE 的 PDB 6SAM，不要进行对接",
    "Find BCHE PDB 6SAM without docking",
    "查找 BCHE 的 PDB: 6sam；请勿执行对接。",
    "Find BCHE PDB: 6SAM; do not dock.",
    "Find BCHE PDB ID: 6SAM, without performing docking!",
    "不要进行对接，查找 BCHE 的 PDB 6SAM",
    "Without docking, find BCHE PDB 6SAM",
    "Do not dock; find BCHE PDB: 6SAM",
])
def test_separate_negative_docking_instruction_keeps_exact_lookup(query):
    assert requested_pdb_id(query) == "6SAM"


@pytest.mark.parametrize("query", [
    "不要查 PDB 6SAM",
    "PDB 6SAM 不要查",
    "not PDB 6SAM",
    "不要查PDB6SAM",
    "PDB6SAM不要查",
    "not PDB6SAM",
    "不要查找 BCHE，PDB: 6SAM",
    "Do not search BCHE, PDB: 6SAM",
    "Find BCHE, not PDB: 6SAM",
    "Find BCHE PDB: 6SAM instead",
    "查找 BCHE PDB: 6SAM，改用 1P0I",
    "查找 BCHE PDB: 6SAM，而是 1P0I",
    "Find BCHE PDB: 6SAM except 1P0I",
    "Find BCHE PDB: 6SAM without experimental evidence",
    "不要查找 BCHE PDB: 6SAM，不要进行对接",
    "Do not look up BCHE PDB: 6SAM without docking",
    "Without docking, do not find BCHE PDB: 6SAM",
    "不要进行对接，也不要查 BCHE PDB: 6SAM",
    "Do not dock or search BCHE PDB: 6SAM",
    "Do not dock and search BCHE PDB: 6SAM",
    "Do not dock, or search BCHE PDB: 6SAM",
    "Find BCHE without docking PDB: 6SAM",
    "Find BCHE PDB: 6SAM, not docking or lookup",
    "查找 BCHE PDB: 6SAM，不要进行对接和查询",
    "Find BCHE PDB: 6SAM, 1P0I without docking",
    "Find BCHE PDB: 6SAM or 1P0I without docking",
    "Find BCHE PDB: 6SAM PDB: 1P0I without docking",
    "查找 BCHE PDB: 6SAM、1P0I，不要进行对接",
    "Find BCHE PDB: 6SAM or PDB1P0I without docking",
    "Find BCHE PDB: 6SAM or PDB ID1P0I without docking",
    "Find BCHE PDB1P0I or PDB: 6SAM without docking",
    "Find BCHE PDB: 6SAM or PDB1P0I",
])
def test_negative_or_ambiguous_lookup_cannot_become_positive(query):
    with pytest.raises(ValueError):
        requested_pdb_id(query)
    with pytest.raises(ValueError):
        target_text_without_pdb(query)


def test_target_text_removal_preserves_unrelated_negative_instruction():
    assert target_text_without_pdb(
        "查找 BCHE 的 PDB 6SAM，不要进行对接"
    ) == "查找 BCHE 的 不要进行对接"
    assert target_text_without_pdb(
        "Find BCHE PDB 6SAM without docking"
    ) == "Find BCHE without docking"


@pytest.mark.parametrize("value", ["6SAMX", "../6SAM", "6SAM.cif", "6SA"])
def test_unrelated_negative_instruction_does_not_relax_identifier_validation(value):
    query = f"Find BCHE PDB: {value} without docking"
    with pytest.raises(ValueError, match="invalid_pdb_constraint"):
        requested_pdb_id(query)
    with pytest.raises(ValueError, match="invalid_pdb_constraint"):
        target_text_without_pdb(query)


def test_existing_unqualified_and_structured_constraints_are_unchanged():
    assert requested_pdb_id("Find BCHE PDB: 6sam") == "6SAM"
    assert requested_pdb_id({"gene_symbol": "BCHE", "pdb_id": "6sam"}) == "6SAM"
    assert requested_pdb_id("Find BCHE without docking") is None
    assert target_text_without_pdb("Find BCHE without docking") == "Find BCHE without docking"


@pytest.mark.parametrize("query", ["Find BCHE PDB6SAM", "Find BCHE PDB ID6SAM"])
def test_compact_label_is_not_new_positive_identifier_grammar(query):
    assert requested_pdb_id(query) is None
    assert not has_pdb_constraint(query)
    assert target_text_without_pdb(query) == query
