import pytest

from src.agent.capabilities import CAPABILITY_CATALOG, capability_for_tool


def test_capability_catalog_maps_real_scientific_tools():
    assert capability_for_tool("property_calculator").name == "molecule.properties"
    assert capability_for_tool("llm_molecular_generator").name == "molecule.generate"
    assert capability_for_tool("molecular_docking").approval_required is True
    assert capability_for_tool("activity_predictor").scientific_result is True


def test_unknown_tool_has_no_implicit_capability():
    with pytest.raises(KeyError, match="Unknown tool capability"):
        capability_for_tool("run_arbitrary_python")


def test_capability_names_are_unique():
    names = [item.name for item in CAPABILITY_CATALOG]
    assert len(names) == len(set(names))
