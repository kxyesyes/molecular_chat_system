from pathlib import Path

import pytest


ROUTE_MODULES = (
    "activity_model_routes",
    "activity_prediction_routes",
    "molecule_utility_routes",
    "docking_report_routes",
    "agent_metrics_routes",
    "molecule_properties_routes",
    "docking_routes",
    "admet_routes",
    "reverse_target_routes",
)


def test_legacy_dependency_lookup_prefers_explicit_value_and_stays_dynamic():
    from src.web.routes.route_compat import lazy_dependency

    legacy = type("Legacy", (), {"value": "before"})()
    explicit = object()

    assert lazy_dependency(explicit, legacy, "value", label="value")() is explicit

    getter = lazy_dependency(None, legacy, "value", label="value")
    assert getter() == "before"
    legacy.value = "after"
    assert getter() == "after"


@pytest.mark.parametrize("module_name", ROUTE_MODULES)
def test_selected_routes_keep_support_access_in_compatibility_adapter(module_name):
    source = (
        Path(__file__).parents[1] / "src" / "web" / "routes" / f"{module_name}.py"
    ).read_text(encoding="utf-8")

    assert "from .route_compat import lazy_dependency" in source
    assert "_support." not in source
    assert "getattr(_support" not in source
