"""Design persistence must use the application's real browser-session wiring."""
import hashlib
import importlib
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest

from src.web.agent_session import COOKIE_NAME
from src.web.agent_session_config import setup_agent_sessions
from src.web.request_auth import require_browser_session


@pytest.fixture
def design_app(tmp_path, monkeypatch):
    from src.web.routes import design_routes

    monkeypatch.setenv("MEDCHAT_AGENT_SESSION_DB", str(tmp_path / "sessions.sqlite"))
    monkeypatch.setattr(design_routes, "_SAVE_DIR", str(tmp_path / "saved"))
    app = FastAPI()
    setup_agent_sessions(app)
    design_routes.setup_design_routes(app)

    @app.get("/molecular-design")
    def page(request: Request):
        return {"owner": require_browser_session(request)}

    return app


def test_design_page_initializes_a_persistent_browser_owner(design_app):
    with TestClient(design_app, base_url="http://localhost") as client:
        page = client.get("/molecular-design")
        assert page.status_code == 200
        assert client.cookies.get(COOKIE_NAME)
        assert page.headers["cache-control"] == "private, no-store"
        assert client.get("/molecular-design").json() == page.json()


def test_saved_design_uses_real_session_cookie_and_isolates_clients(design_app, tmp_path):
    pytest.importorskip("rdkit")
    owners = []
    for _ in range(2):
        with TestClient(design_app, base_url="http://localhost") as client:
            # Direct API entry must also initialize a session.
            properties = client.post("/api/design/properties", json={"smiles": "CCO"}).json()["properties"]
            response = client.post("/api/design/save_molecule", json={
                "smiles": "CCO", "properties": properties, "session_id": "forged",
            })
            assert response.status_code == 200
            token = client.cookies.get(COOKIE_NAME)
            owner = design_app.state.agent_session_store.resolve(token)
            assert owner and owner != "forged"
            owners.append(owner)
            path = tmp_path / "saved" / hashlib.sha256(owner.encode()).hexdigest()
            assert (path / "saved_molecules.csv").is_file()
    assert owners[0] != owners[1]


def test_design_export_works_through_application_session_boundary(design_app):
    pytest.importorskip("rdkit")
    with TestClient(design_app, base_url="http://localhost") as client:
        response = client.post("/api/design/export_history", json={
            "history": [{"step": 1, "smi": "CCO", "props": {}}],
        })
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "CCO" in response.text
        assert client.cookies.get(COOKIE_NAME)


@pytest.mark.parametrize("endpoint", ["save_molecule", "export_history"])
def test_design_persistence_rejects_cross_origin_posts(design_app, endpoint, tmp_path):
    response = TestClient(design_app, base_url="http://localhost").post(
        "/api/design/" + endpoint,
        json={},
        headers={"origin": "https://untrusted.example"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Origin not allowed"
    assert not (tmp_path / "sessions.sqlite").exists()


def test_stateless_design_routes_do_not_require_a_session(design_app, tmp_path):
    response = TestClient(design_app).get("/api/design/fragments?page_size=1")
    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    assert not (tmp_path / "sessions.sqlite").exists()


def test_import_does_not_initialize_real_design_storage(monkeypatch):
    save_root = Path(__file__).resolve().parents[1] / "data" / "design_results"
    mutations = []
    original_mkdir = Path.mkdir
    original_chmod = Path.chmod

    def guarded_mkdir(path, *args, **kwargs):
        if path == save_root:
            mutations.append("mkdir")
            return
        return original_mkdir(path, *args, **kwargs)

    def guarded_chmod(path, *args, **kwargs):
        if path == save_root:
            mutations.append("chmod")
            return
        return original_chmod(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", guarded_mkdir)
    monkeypatch.setattr(Path, "chmod", guarded_chmod)
    from src.web.routes import design_routes

    importlib.reload(design_routes)
    assert mutations == []
