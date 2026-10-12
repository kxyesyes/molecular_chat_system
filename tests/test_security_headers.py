from types import SimpleNamespace

import pytest

from src.web.security.headers import apply_security_headers


def test_security_headers_are_applied_without_enabling_hsts_on_local_http():
    response = SimpleNamespace(headers={})

    apply_security_headers(response, secure=False)

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Permissions-Policy"] == "camera=(), geolocation=(), microphone=()"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "object-src 'none'" in response.headers["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in response.headers


def test_security_headers_enable_hsts_only_for_https():
    response = SimpleNamespace(headers={})

    apply_security_headers(response, secure=True)

    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"


def test_app_allows_only_its_ketcher_entrypoint_to_be_framed(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.web.app import MolecularChatApp

    monkeypatch.setenv("AGENT_STATE_DB", str(tmp_path / "agent.sqlite3"))
    application = MolecularChatApp(config_path=str(tmp_path / "missing.yaml"))
    client = TestClient(application.app, base_url="https://localhost")

    editor = client.get("/static/ketcher/index.html?hiddenControls=help")
    assert editor.status_code == 200
    assert "Ketcher" in editor.text
    assert editor.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in editor.headers["Content-Security-Policy"]
    assert "object-src 'none'" in editor.headers["Content-Security-Policy"]
    assert editor.headers["X-Content-Type-Options"] == "nosniff"
    assert "Strict-Transport-Security" in editor.headers

    for path in ("/molecular-design", "/static/ketcher/iframe.html", "/not-found"):
        response = client.get(path)
        assert response.headers["X-Frame-Options"] == "DENY", path
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"], path


@pytest.mark.parametrize("request_path", [
    "/", "/api/tasks", "/static/ketcher/", "/static/ketcher/index.html/extra",
    "/static/ketcher/index.html.evil", "/static/ketcher-other/index.html",
])
def test_frame_exception_is_not_a_prefix_match(request_path):
    response = SimpleNamespace(headers={})
    apply_security_headers(response, secure=False, request_path=request_path)
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_explicit_stricter_headers_are_preserved():
    response = SimpleNamespace(headers={
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": "frame-ancestors 'none'",
    })
    apply_security_headers(response, secure=False, request_path="/static/ketcher/index.html")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
