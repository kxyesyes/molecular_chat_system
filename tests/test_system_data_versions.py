import asyncio
from contextlib import closing
import sqlite3
import threading

import httpx
from fastapi import FastAPI

from src.system.data_versions import collect_data_versions


def test_collect_data_versions_reports_core_assets(tmp_path, monkeypatch):
    for name in ("TARGET_DB_PATH", "TARGET_CACHE_DIR", "REVERSE_TARGET_DATA_DIR", "ACTIVITY_MODEL_DIR"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path
    target_dir = root / "data" / "target_db"
    target_dir.mkdir(parents=True)
    cache_dir = target_dir / "cache"
    cache_dir.mkdir()
    (cache_dir / "demo.cif").write_text("data", encoding="utf-8")

    db_path = target_dir / "target_database.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE targets (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE target_structures (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO targets DEFAULT VALUES")
        conn.execute("INSERT INTO target_structures DEFAULT VALUES")

    versions = collect_data_versions(project_root=root)

    assert versions["target_db"]["exists"] is True
    assert versions["target_db"]["target_count"] == 1
    assert versions["target_db"]["structure_count"] == 1
    assert versions["target_cache"]["file_count"] == 1
    assert "reverse_target" in versions
    assert "activity_models" in versions


def test_mounted_assets_report_live_counts_under_logical_paths(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    mount = tmp_path / "private mount"
    mount.mkdir()
    db_path = mount / "custom #1.sqlite"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(
            "CREATE TABLE targets (id INTEGER); INSERT INTO targets VALUES (1), (2);"
            "CREATE TABLE target_structures (id INTEGER); INSERT INTO target_structures VALUES (1);"
        )
    monkeypatch.setenv("TARGET_DB_PATH", str(db_path))
    for variable in ("TARGET_CACHE_DIR", "REVERSE_TARGET_DATA_DIR", "ACTIVITY_MODEL_DIR"):
        directory = mount / variable
        directory.mkdir()
        (directory / "fixture").touch()
        monkeypatch.setenv(variable, str(directory))

    connect = sqlite3.connect

    def readonly_connection(database, **kwargs):
        assert database.endswith("?mode=ro") and kwargs["uri"] is True
        return connect(database, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", readonly_connection)
    versions = collect_data_versions(root)
    assert versions["target_db"]["target_count"] == 2
    assert versions["target_db"]["structure_count"] == 1
    assert versions["target_db"]["path"] == "data/target_db/target_database.sqlite"
    for key in ("target_cache", "reverse_target", "activity_models"):
        assert versions[key]["file_count"] == 1
    assert mount.name not in str(versions)


def test_versions_route_offloads_inventory(monkeypatch):
    from src.web.routes import system_routes

    loop_thread = threading.get_ident()
    monkeypatch.setattr(system_routes, "collect_data_versions",
                        lambda: {"off_loop": threading.get_ident() != loop_thread})
    app = FastAPI()
    system_routes.setup_system_routes(app)

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/system/data-versions")
        assert response.status_code == 200
        assert response.json()["data"] == {"off_loop": True}

    asyncio.run(check())
