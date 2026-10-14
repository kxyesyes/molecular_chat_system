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


def test_activity_budget_helper_has_no_support_container_parameter():
    """The private budget runner is a concrete adapter, not a support facade."""

    import inspect

    from src.web.routes.activity_prediction_routes import _invoke_activity_with_budget

    assert "_support" not in inspect.signature(_invoke_activity_with_budget).parameters


@pytest.mark.parametrize(
    ("module_name", "register_name"),
    (
        ("activity_model_routes", "register_activity_model_routes"),
        ("activity_prediction_routes", "register_activity_prediction_routes"),
        ("molecule_utility_routes", "register_molecule_utility_routes"),
        ("docking_report_routes", "register_docking_report_routes"),
    ),
)
def test_production_registration_has_no_legacy_support_parameter(module_name, register_name):
    import importlib
    import inspect

    module = importlib.import_module(f"src.web.routes.{module_name}")
    register = getattr(module, register_name)
    assert "_support" not in inspect.signature(register).parameters


@pytest.mark.parametrize("module_name", ROUTE_MODULES)
def test_selected_routes_keep_support_access_in_compatibility_adapter(module_name):
    source = (
        Path(__file__).parents[1] / "src" / "web" / "routes" / f"{module_name}.py"
    ).read_text(encoding="utf-8")

    assert "from .route_compat import lazy_dependency" in source
    assert "_support." not in source
    assert "getattr(_support" not in source
