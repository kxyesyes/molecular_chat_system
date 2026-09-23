"""Exercise page rendering against real Jinja and supported call signatures."""

from inspect import signature

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from src.web.routes import main_routes


PAGES = [
    ("/", "index.html", "Molecular Chat System API"),
    ("/molecular-docking", "molecular_docking.html", "Molecular Docking System"),
    ("/reverse-docking", "reverse_target.html", "Reverse Target Prediction System"),
    ("/reverse-target", "reverse_target.html", "Reverse Target Prediction System"),
    ("/activity-prediction", "activity_prediction.html", "Activity Prediction System"),
    ("/kermt-admet", "kermt_admet.html", "KERMT ADMET Prediction System"),
    ("/molecular-design", "molecular_design.html", "Molecular Design System"),
    ("/target-search", "target_search.html", "Target Search Demo"),
]


@pytest.fixture
def templates(tmp_path):
    for name in {page[1] for page in PAGES}:
        (tmp_path / name).write_text(
            name + " | {{ request.url.path }} | {{ url_for('home') }}"
            " | {{ request.query_params.get('q', '') }}",
            encoding="utf-8",
        )
    return Jinja2Templates(directory=tmp_path)


@pytest.mark.parametrize("path,name,message", PAGES)
def test_real_templates_preserve_request_url_for_and_escaping(
    templates, path, name, message
):
    app = FastAPI()
    main_routes.register_main_routes(app, templates)
    with TestClient(app) as client:
        response = client.get(path, params={"q": "<script>alert(1)</script>"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == (
        f"{name} | {path} | http://testserver/"
        " | &lt;script&gt;alert(1)&lt;/script&gt;"
    )
    assert "<script>" not in response.text


@pytest.mark.parametrize("path,name,message", PAGES)
def test_without_templates_preserves_fallback_json(path, name, message):
    app = FastAPI()
    main_routes.register_main_routes(app, None)
    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == 200
    assert response.json() == {"message": message, "status": "running"}


def test_template_render_type_error_propagates_without_retry(tmp_path):
    (tmp_path / "index.html").write_text("{{ fail_render() }}", encoding="utf-8")
    templates = Jinja2Templates(directory=tmp_path)
    calls = []
    error = TypeError("template-render-failed")

    def fail_render():
        calls.append(True)
        raise error

    templates.env.globals["fail_render"] = fail_render
    app = FastAPI()
    main_routes.register_main_routes(app, templates)
    with TestClient(app) as client:
        with pytest.raises(TypeError, match="^template-render-failed$") as caught:
            client.get("/")

    assert caught.value is error
    assert len(calls) == 1


class LegacyTemplates:
    def __init__(self):
        self.calls = []

    def TemplateResponse(self, name, context):
        self.calls.append({"name": name, "context": context.copy()})
        return Response("rendered")


class ModernTemplates:
    def __init__(self):
        self.calls = []

    def TemplateResponse(self, request, name, context):
        self.calls.append({"request": request, "name": name, "context": context.copy()})
        return Response("rendered")


class TransitionalTemplates:
    def __init__(self):
        self.calls = []

    def TemplateResponse(self, *args, **kwargs):
        assert args == ()
        self.calls.append(kwargs)
        return Response("rendered")


@pytest.mark.parametrize("template_class", [LegacyTemplates, ModernTemplates, TransitionalTemplates])
@pytest.mark.parametrize("path,name,message", PAGES)
def test_explicit_and_transitional_signatures(template_class, path, name, message):
    templates = template_class()
    app = FastAPI()
    main_routes.register_main_routes(app, templates)
    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == 200
    assert response.text == "rendered"
    assert len(templates.calls) == 1
    call = templates.calls[0]
    if template_class is ModernTemplates:
        request = call["request"]
        assert call == {"request": request, "name": name, "context": {}}
    else:
        request = call["context"]["request"]
        assert call == {"name": name, "context": {"request": request}}
    assert isinstance(request, Request)
    assert request.url.path == path
    assert request.app is app


@pytest.mark.parametrize("with_templates", [True, False])
def test_signature_checked_only_at_registration(templates, monkeypatch, with_templates):
    checked = []

    def count_signature(method):
        checked.append(method)
        return signature(method)

    monkeypatch.setattr(main_routes, "signature", count_signature)
    app = FastAPI()
    main_routes.register_main_routes(app, templates if with_templates else None)
    expected = [templates.TemplateResponse] if with_templates else []
    assert checked == expected
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/target-search").status_code == 200
    assert checked == expected
