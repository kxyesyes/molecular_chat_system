"""RED contract tests for the ADMET research-console page routes."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates

from src.web.routes.main_routes import register_main_routes


def test_admet_page_is_the_only_admet_entrypoint():
    app = FastAPI()
    templates = Jinja2Templates(directory="src/web/templates")
    register_main_routes(app, templates)
    paths = {route.path for route in app.routes}

    assert "/admet" in paths
    assert "/kermt-admet" not in paths

    with TestClient(app) as client:
        response = client.get("/admet")
    assert response.status_code == 200
    assert "ADMET 研究控制台" in response.text


def test_page_routes_inventory_has_no_removed_path():
    from src.web.routes.page_routes import _MAIN_PAGE_PATHS

    assert "/admet" in _MAIN_PAGE_PATHS
    assert "/kermt-admet" not in _MAIN_PAGE_PATHS
