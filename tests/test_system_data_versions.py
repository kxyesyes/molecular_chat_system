import sqlite3

from src.system.data_versions import collect_data_versions


def test_collect_data_versions_reports_core_assets(tmp_path):
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
