import hashlib
import json
import os
import sqlite3
import threading
from contextlib import nullcontext
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


class MutableClock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture
def project_root(tmp_path):
    return tmp_path


@pytest.fixture(autouse=True)
def isolate_target_runtime(tmp_path, monkeypatch):
    from src.target_search.database import get_cache_dir, get_db_path

    monkeypatch.setenv("TARGET_DB_PATH", str(tmp_path / "runtime" / "targets.sqlite"))
    monkeypatch.setenv("TARGET_CACHE_DIR", str(tmp_path / "runtime" / "cache"))
    assert get_db_path(tmp_path).is_relative_to(tmp_path)
    assert get_cache_dir(tmp_path).is_relative_to(tmp_path)


@pytest.fixture
def clock():
    return MutableClock(datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc))


def test_init_db_adds_authoritative_cache_tables_idempotently(project_root):
    from src.target_search.database import get_connection, init_db

    init_db(project_root)
    init_db(project_root)

    conn = get_connection(project_root)
    try:
        cache_columns = {
            row["name"]: row for row in conn.execute("PRAGMA table_info(target_remote_cache)")
        }
        state_columns = {
            row["name"]: row for row in conn.execute("PRAGMA table_info(target_runtime_state)")
        }
        indexes = {
            row["name"] for row in conn.execute("PRAGMA index_list(target_remote_cache)")
        }
    finally:
        conn.close()

    assert {
        "cache_key",
        "record_type",
        "source",
        "source_record_id",
        "payload_json",
        "payload_digest",
        "retrieved_at",
        "expires_at",
        "last_refresh_status",
        "schema_version",
        "expires_epoch",
        "cleanup_pending",
    }.issubset(cache_columns)
    assert cache_columns["cache_key"]["pk"] == 1
    assert cache_columns["schema_version"]["dflt_value"] == "1"
    assert {"state_key", "state_value", "updated_at"}.issubset(state_columns)
    assert state_columns["state_key"]["pk"] == 1
    assert "idx_target_remote_cache_expires_at" in indexes

    conn = get_connection(project_root)
    try:
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'target_remote_cache'"
        ).fetchone()["sql"]
    finally:
        conn.close()
    normalized_sql = " ".join(table_sql.upper().split())
    assert "CACHE_KEY TEXT PRIMARY KEY NOT NULL" in normalized_sql
    assert "CHECK" in normalized_sql


def test_init_db_idempotently_migrates_existing_cache_expiry_metadata(project_root):
    from src.target_search.database import get_db_path, init_db

    db_path = get_db_path(project_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    payload_json = '{"gene":"EGFR"}'
    digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE target_remote_cache (
                cache_key TEXT PRIMARY KEY,
                record_type TEXT,
                source TEXT,
                source_record_id TEXT,
                payload_json TEXT,
                payload_digest TEXT,
                retrieved_at TEXT,
                expires_at TEXT,
                last_refresh_status TEXT,
                schema_version INTEGER DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            INSERT INTO target_remote_cache VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 1)
            """,
            (
                "legacy:P00533",
                "target",
                "UniProt",
                "P00533",
                payload_json,
                digest,
                "2026-01-01T11:00:00-01:00",
                "2026-01-01T11:30:00-01:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    init_db(project_root)
    init_db(project_root)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(target_remote_cache)")}
        row = conn.execute(
            "SELECT retrieved_at, expires_at, expires_epoch, cleanup_pending "
            "FROM target_remote_cache WHERE cache_key = ?",
            ("legacy:P00533",),
        ).fetchone()
    finally:
        conn.close()

    assert {"expires_epoch", "cleanup_pending"}.issubset(columns)
    assert row["retrieved_at"] == "2026-01-01T12:00:00+00:00"
    assert row["expires_at"] == "2026-01-01T12:30:00+00:00"
    assert row["expires_epoch"] == datetime(
        2026, 1, 1, 12, 30, tzinfo=timezone.utc
    ).timestamp()
    assert row["cleanup_pending"] == 0


def test_upsert_applies_record_ttls_serializes_deterministically_and_is_immutable(
    project_root, clock
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_connection

    repository = TargetCacheRepository(project_root=project_root, clock=clock)
    target = repository.upsert(
        cache_key="uniprot:P00533",
        record_type="target",
        source="UniProt",
        source_record_id="P00533",
        payload={"z": 2, "a": 1},
    )
    structures = repository.upsert(
        cache_key="rcsb:P00533",
        record_type="structures",
        source="RCSB_PDB",
        source_record_id="P00533",
        payload={"structures": ["4WKQ"]},
    )

    assert target.retrieved_at == clock.value
    assert target.expires_at == clock.value + timedelta(days=30)
    assert structures.expires_at == clock.value + timedelta(days=7)
    assert target.retrieved_at.tzinfo is timezone.utc
    assert target.expires_at.tzinfo is timezone.utc
    assert target.stale is False
    assert target.payload == {"a": 1, "z": 2}
    with pytest.raises(FrozenInstanceError):
        target.cache_key = "changed"
    with pytest.raises(TypeError):
        target.payload["a"] = 3
    serialized = target.to_dict()
    assert json.loads(json.dumps(serialized))["payload"] == {"a": 1, "z": 2}
    assert serialized["retrieved_at"] == clock.value.isoformat()
    assert serialized["expires_at"] == (clock.value + timedelta(days=30)).isoformat()
    assert serialized["stale"] is False
    serialized["payload"]["a"] = 99
    assert target.payload["a"] == 1

    conn = get_connection(project_root)
    try:
        row = conn.execute(
            "SELECT payload_json, payload_digest FROM target_remote_cache WHERE cache_key = ?",
            (target.cache_key,),
        ).fetchone()
    finally:
        conn.close()

    assert row["payload_json"] == '{"a":1,"z":2}'
    assert row["payload_digest"] == hashlib.sha256(
        row["payload_json"].encode("utf-8")
    ).hexdigest()


def test_upsert_atomically_replaces_existing_cache_record(project_root, clock):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_connection

    repository = TargetCacheRepository(project_root=project_root, clock=clock)
    repository.upsert(
        "uniprot:P00533",
        "target",
        "UniProt",
        "old-id",
        {"version": 1},
    )
    clock.value += timedelta(hours=2)

    replaced = repository.upsert(
        "uniprot:P00533",
        "target",
        "UniProt",
        "new-id",
        {"version": 2},
    )

    conn = get_connection(project_root)
    try:
        rows = conn.execute(
            "SELECT * FROM target_remote_cache WHERE cache_key = ?",
            ("uniprot:P00533",),
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    assert rows[0]["source_record_id"] == "new-id"
    assert rows[0]["last_refresh_status"] is None
    assert replaced.payload == {"version": 2}
    assert replaced.retrieved_at == clock.value


def test_upsert_materializes_its_generation_before_a_second_writer_commits(
    project_root, clock, monkeypatch
):
    from src.target_search import cache as cache_module

    repository_a = cache_module.TargetCacheRepository(project_root=project_root, clock=clock)
    repository_b = cache_module.TargetCacheRepository(project_root=project_root, clock=clock)
    real_get_connection = cache_module.get_connection
    first_committed = threading.Event()
    second_finished = threading.Event()
    results = {}
    errors = []

    class CommitBarrierConnection:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            result = self.connection.__exit__(exc_type, exc_value, traceback)
            if exc_type is None:
                first_committed.set()
                assert second_finished.wait(5), "second writer did not finish"
            return result

    def coordinated_connection(root):
        connection = real_get_connection(root)
        if threading.current_thread().name == "cache-writer-a":
            return CommitBarrierConnection(connection)
        return connection

    monkeypatch.setattr(cache_module, "get_connection", coordinated_connection)
    monkeypatch.setattr(cache_module, "_CACHE_MUTATION_LOCK", nullcontext(), raising=False)

    def write_a():
        try:
            results["a"] = repository_a.upsert(
                "uniprot:P00533", "target", "UniProt", "A", {"writer": "a"}
            )
        except Exception as exc:
            errors.append(exc)

    def write_b():
        try:
            results["b"] = repository_b.upsert(
                "uniprot:P00533", "target", "UniProt", "B", {"writer": "b"}
            )
        except Exception as exc:
            errors.append(exc)
        finally:
            second_finished.set()

    first = threading.Thread(target=write_a, name="cache-writer-a")
    first.start()
    assert first_committed.wait(5), "first writer did not reach commit barrier"
    second = threading.Thread(target=write_b, name="cache-writer-b")
    second.start()
    second.join(5)
    first.join(5)

    assert not errors
    assert not first.is_alive() and not second.is_alive()
    assert results["a"].payload == {"writer": "a"}
    assert results["b"].payload == {"writer": "b"}
    assert repository_b.get("uniprot:P00533").payload == {"writer": "b"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"cache_key": None},
        {"cache_key": ""},
        {"cache_key": "x" * 513},
        {"source": None},
        {"source": "\n"},
        {"source_record_id": ""},
        {"record_type": "unknown", "ttl": timedelta(days=1)},
        {"ttl": timedelta(0)},
        {"ttl": "one day"},
        {"payload": {"score": float("nan")}},
        {
            "record_type": "coordinate_file",
            "payload": {"path": str(Path("C:/outside/structure.cif"))},
        },
        {
            "record_type": "coordinate_file",
            "payload": {"path": "../outside/structure.cif"},
        },
        {
            "record_type": "coordinate_file",
            "payload": {"path": "coordinates/structure.cif:alternate"},
        },
    ],
)
def test_upsert_rejects_invalid_inputs_before_opening_a_connection(
    project_root, clock, monkeypatch, overrides
):
    from src.target_search import cache as cache_module

    repository = cache_module.TargetCacheRepository(project_root=project_root, clock=clock)
    parameters = {
        "cache_key": "uniprot:P00533",
        "record_type": "target",
        "source": "UniProt",
        "source_record_id": "P00533",
        "payload": {"gene": "EGFR"},
    }
    parameters.update(overrides)

    def connection_must_not_open(_root):
        raise AssertionError("invalid input opened a database connection")

    monkeypatch.setattr(cache_module, "get_connection", connection_must_not_open)
    with pytest.raises((TypeError, ValueError)):
        repository.upsert(**parameters)


def test_upsert_accepts_bounded_unicode_identifiers(project_root, clock):
    from src.target_search.cache import TargetCacheRepository

    evidence = TargetCacheRepository(project_root, clock).upsert(
        "靶点:受体-1", "target", "联合数据库", "记录-α", {"gene": "受体"}
    )

    assert evidence.cache_key == "靶点:受体-1"
    assert evidence.source == "联合数据库"
    assert evidence.source_record_id == "记录-α"


def test_get_rejects_unknown_schema_versions(project_root, clock):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_connection

    repository = TargetCacheRepository(project_root, clock)
    repository.upsert(
        "uniprot:P00533", "target", "UniProt", "P00533", {"gene": "EGFR"}
    )
    conn = get_connection(project_root)
    try:
        conn.execute(
            "UPDATE target_remote_cache SET schema_version = 2 WHERE cache_key = ?",
            ("uniprot:P00533",),
        )
        conn.commit()
    finally:
        conn.close()

    assert repository.get("uniprot:P00533", allow_stale=True) is None


def test_get_returns_fresh_rejects_expired_and_can_return_stale(project_root, clock):
    from src.target_search.cache import TargetCacheRepository

    repository = TargetCacheRepository(project_root=project_root, clock=clock)
    repository.upsert(
        "rcsb:P00533",
        "structures",
        "RCSB_PDB",
        "P00533",
        {"structures": ["4WKQ"]},
    )

    fresh = repository.get("rcsb:P00533")
    clock.value += timedelta(days=7)
    expired = repository.get("rcsb:P00533")
    stale = repository.get("rcsb:P00533", allow_stale=True)

    assert fresh is not None
    assert fresh.stale is False
    assert expired is None
    assert stale is not None
    assert stale.stale is True
    assert stale.payload == {"structures": ("4WKQ",)}
    assert repository.get("missing") is None


@pytest.mark.parametrize(
    ("column", "corrupt_value"),
    [
        ("payload_json", "{not-json"),
        ("payload_digest", "0" * 64),
        ("retrieved_at", "not-a-timestamp"),
        ("expires_at", "2026-01-15T12:00:00"),
    ],
)
def test_get_rejects_corrupt_json_digest_and_timestamps(
    project_root, clock, column, corrupt_value
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_connection

    repository = TargetCacheRepository(project_root=project_root, clock=clock)
    repository.upsert(
        "uniprot:P00533",
        "target",
        "UniProt",
        "P00533",
        {"gene": "EGFR"},
    )
    conn = get_connection(project_root)
    try:
        conn.execute(
            f"UPDATE target_remote_cache SET {column} = ? WHERE cache_key = ?",
            (corrupt_value, "uniprot:P00533"),
        )
        conn.commit()
    finally:
        conn.close()

    assert repository.get("uniprot:P00533", allow_stale=True) is None


def _stored_refresh_status(project_root, repository, reason, expected_digest):
    from src.target_search.database import get_connection

    matched = repository.mark_refresh_failure(
        "uniprot:P00533", reason, expected_digest=expected_digest
    )
    assert matched is True
    conn = get_connection(project_root)
    try:
        row = conn.execute(
            "SELECT last_refresh_status FROM target_remote_cache WHERE cache_key = ?",
            ("uniprot:P00533",),
        ).fetchone()
    finally:
        conn.close()
    return row["last_refresh_status"]


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (" provider_error ", "provider_error"),
        ("TIMEOUT", "timeout"),
        ("rate_limited:http_429", "rate_limited:http_429"),
        ("invalid_response:http_502", "invalid_response:http_502"),
        ("provider_error:http_503", "provider_error:http_503"),
        ("timeout:http_999", "remote_refresh_failed"),
        ("t\u0131meout", "remote_refresh_failed"),
        ("unknown provider failure", "remote_refresh_failed"),
    ],
)
def test_mark_refresh_failure_persists_only_normalized_allowlisted_statuses(
    project_root, clock, reason, expected
):
    from src.target_search.cache import TargetCacheRepository

    repository = TargetCacheRepository(project_root=project_root, clock=clock)
    evidence = repository.upsert(
        "uniprot:P00533",
        "target",
        "UniProt",
        "P00533",
        {"gene": "EGFR"},
    )

    status = _stored_refresh_status(
        project_root, repository, reason, evidence.payload_digest
    )

    assert status == expected
    assert len(status) <= 500
    assert "\n" not in status and "\r" not in status


@pytest.mark.parametrize(
    "reason",
    [
        '{"error":"upstream denied","access_token":"json-secret"}',
        "<html><body>upstream failure client_secret=html-secret</body></html>",
        "access_token=access-secret client_secret=client-secret",
        "Authorization: Bearer bearer-secret sk-secret-value api_key=api-secret",
        "HTTP 503\nopaque upstream response contents",
    ],
)
def test_mark_refresh_failure_never_persists_bodies_credentials_or_free_text(
    project_root, clock, reason
):
    from src.target_search.cache import TargetCacheRepository

    repository = TargetCacheRepository(project_root=project_root, clock=clock)
    evidence = repository.upsert(
        "uniprot:P00533",
        "target",
        "UniProt",
        "P00533",
        {"gene": "EGFR"},
    )

    status = _stored_refresh_status(
        project_root, repository, reason, evidence.payload_digest
    )

    assert status == "remote_refresh_failed"
    assert len(status) <= 500
    assert "\n" not in status and "\r" not in status
    assert not any(
        fragment in status
        for fragment in (
            "json-secret",
            "html-secret",
            "access-secret",
            "client-secret",
            "bearer-secret",
            "sk-secret-value",
            "api-secret",
            "upstream",
        )
    )


def test_stale_refresh_failure_cannot_stamp_a_newer_generation(project_root, clock):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_connection

    repository = TargetCacheRepository(project_root=project_root, clock=clock)
    first = repository.upsert(
        "uniprot:P00533", "target", "UniProt", "P00533", {"version": 1}
    )
    second = repository.upsert(
        "uniprot:P00533", "target", "UniProt", "P00533", {"version": 2}
    )

    stale_matched = repository.mark_refresh_failure(
        "uniprot:P00533", "timeout", expected_digest=first.payload_digest
    )
    current_matched = repository.mark_refresh_failure(
        "uniprot:P00533", "timeout", expected_digest=second.payload_digest
    )

    conn = get_connection(project_root)
    try:
        row = conn.execute(
            "SELECT last_refresh_status FROM target_remote_cache WHERE cache_key = ?",
            ("uniprot:P00533",),
        ).fetchone()
    finally:
        conn.close()
    assert stale_matched is False
    assert current_matched is True
    assert row["last_refresh_status"] == "timeout"


def test_cleanup_expired_is_bounded_and_returns_consistent_counts(project_root, clock):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_connection

    repository = TargetCacheRepository(project_root=project_root, clock=clock)
    for index in range(5):
        repository.upsert(
            f"target:{index}",
            "target",
            "UniProt",
            str(index),
            {"index": index},
        )
    clock.value += timedelta(days=31)

    first = repository.cleanup_expired(limit=2)
    second = repository.cleanup_expired(limit=2)

    conn = get_connection(project_root)
    try:
        remaining = conn.execute("SELECT COUNT(*) AS count FROM target_remote_cache").fetchone()[
            "count"
        ]
    finally:
        conn.close()

    assert first == {"records_deleted": 2, "files_deleted": 0}
    assert second == {"records_deleted": 2, "files_deleted": 0}
    assert remaining == 1


def test_cleanup_expired_deletes_only_contained_regular_coordinate_files(
    project_root, clock
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir, get_connection

    repository = TargetCacheRepository(project_root=project_root, clock=clock)
    cache_dir = get_cache_dir(project_root)
    safe_file = cache_dir / "coordinates" / "safe.cif"
    unrelated_file = cache_dir / "unrelated.txt"
    referenced_unrelated_file = cache_dir / "referenced-unrelated.txt"
    directory = cache_dir / "coordinate-directory"
    outside_file = project_root / "outside.cif"
    safe_file.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir(parents=True, exist_ok=True)
    safe_file.write_text("safe", encoding="utf-8")
    unrelated_file.write_text("unrelated", encoding="utf-8")
    referenced_unrelated_file.write_text("referenced unrelated", encoding="utf-8")
    outside_file.write_text("outside", encoding="utf-8")

    entries = [
        (
            "coordinate:safe",
            "coordinate_file",
            {"path": "coordinates/safe.cif"},
        ),
        ("coordinate:outside", "coordinate_file", {"path": "placeholder.cif"}),
        (
            "coordinate:directory",
            "coordinate_file",
            {"path": "coordinate-directory"},
        ),
        (
            "target:unrelated",
            "target",
            {"path": "unrelated.txt"},
        ),
        (
            "coordinate:unrelated-extension",
            "coordinate_file",
            {"path": "referenced-unrelated.txt"},
        ),
    ]

    for cache_key, record_type, payload in entries:
        repository.upsert(
            cache_key,
            record_type,
            "RCSB_PDB",
            cache_key,
            payload,
            ttl=timedelta(hours=1),
        )
    outside_payload = '{"path":"../outside.cif"}'
    conn = get_connection(project_root)
    try:
        conn.execute(
            """
            UPDATE target_remote_cache
            SET payload_json = ?, payload_digest = ?
            WHERE cache_key = ?
            """,
            (
                outside_payload,
                hashlib.sha256(outside_payload.encode("utf-8")).hexdigest(),
                "coordinate:outside",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    clock.value += timedelta(hours=2)

    result = repository.cleanup_expired(limit=100)

    conn = get_connection(project_root)
    try:
        retained = {
            row["cache_key"]: row["cleanup_pending"]
            for row in conn.execute(
                "SELECT cache_key, cleanup_pending FROM target_remote_cache"
            )
        }
    finally:
        conn.close()

    assert result == {"records_deleted": 2, "files_deleted": 1}
    assert not safe_file.exists()
    assert outside_file.read_text(encoding="utf-8") == "outside"
    assert directory.is_dir()
    assert unrelated_file.read_text(encoding="utf-8") == "unrelated"
    assert referenced_unrelated_file.read_text(encoding="utf-8") == "referenced unrelated"
    assert retained == {
        "coordinate:directory": 1,
        "coordinate:outside": 1,
        "coordinate:unrelated-extension": 1,
    }


def test_cleanup_retains_symlink_and_explicitly_skips_when_creation_is_unsupported(
    project_root, clock
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir, get_connection

    repository = TargetCacheRepository(project_root, clock)
    cache_dir = get_cache_dir(project_root)
    outside_file = project_root / "outside.cif"
    escape_link = cache_dir / "escape.cif"
    cache_dir.mkdir(parents=True, exist_ok=True)
    outside_file.write_text("outside", encoding="utf-8")
    try:
        os.symlink(outside_file, escape_link)
    except OSError as exc:
        pytest.skip(f"symlink creation unsupported: {exc}")

    repository.upsert(
        "coordinate:escape-link",
        "coordinate_file",
        "RCSB_PDB",
        "escape-link",
        {"path": "escape.cif"},
        ttl=timedelta(hours=1),
    )
    clock.value += timedelta(hours=2)

    result = repository.cleanup_expired()

    conn = get_connection(project_root)
    try:
        row = conn.execute(
            "SELECT cleanup_pending FROM target_remote_cache WHERE cache_key = ?",
            ("coordinate:escape-link",),
        ).fetchone()
    finally:
        conn.close()
    assert result == {"records_deleted": 0, "files_deleted": 0}
    assert row["cleanup_pending"] == 1
    assert escape_link.is_symlink()
    assert outside_file.read_text(encoding="utf-8") == "outside"


@pytest.mark.parametrize(
    ("column", "corrupt_value"),
    [
        (
            "payload_json",
            '{"path":"coordinates/tampered.cif"}',
        ),
        ("retrieved_at", "not-a-timestamp"),
    ],
)
def test_cleanup_never_deletes_files_for_corrupt_cache_rows(
    project_root, clock, column, corrupt_value
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir, get_connection

    repository = TargetCacheRepository(project_root=project_root, clock=clock)
    coordinate_dir = get_cache_dir(project_root) / "coordinates"
    original_file = coordinate_dir / "original.cif"
    tampered_file = coordinate_dir / "tampered.cif"
    coordinate_dir.mkdir(parents=True, exist_ok=True)
    original_file.write_text("original", encoding="utf-8")
    tampered_file.write_text("tampered", encoding="utf-8")
    repository.upsert(
        "coordinate:corrupt",
        "coordinate_file",
        "RCSB_PDB",
        "corrupt",
        {"path": "coordinates/original.cif"},
        ttl=timedelta(hours=1),
    )
    clock.value += timedelta(hours=2)

    conn = get_connection(project_root)
    try:
        conn.execute(
            f"UPDATE target_remote_cache SET {column} = ? WHERE cache_key = ?",
            (corrupt_value, "coordinate:corrupt"),
        )
        conn.commit()
    finally:
        conn.close()

    result = repository.cleanup_expired()

    conn = get_connection(project_root)
    try:
        retained = conn.execute(
            "SELECT cleanup_pending FROM target_remote_cache WHERE cache_key = ?",
            ("coordinate:corrupt",),
        ).fetchone()
    finally:
        conn.close()

    assert result == {"records_deleted": 0, "files_deleted": 0}
    assert retained["cleanup_pending"] == 1
    assert original_file.read_text(encoding="utf-8") == "original"
    assert tampered_file.read_text(encoding="utf-8") == "tampered"


def test_cleanup_tombstone_commits_before_unlink_and_failed_delete_is_retriable(
    project_root, clock, monkeypatch
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir, get_db_path, get_connection

    repository = TargetCacheRepository(project_root, clock)
    coordinate_file = get_cache_dir(project_root) / "coordinates" / "fault.cif"
    coordinate_file.parent.mkdir(parents=True, exist_ok=True)
    coordinate_file.write_text("fault", encoding="utf-8")
    repository.upsert(
        "coordinate:fault",
        "coordinate_file",
        "RCSB_PDB",
        "fault",
        {"path": "coordinates/fault.cif"},
        ttl=timedelta(hours=1),
    )
    clock.value += timedelta(hours=2)
    observed = {"write_lock_released": False}

    def injected_failure(_payload):
        probe = sqlite3.connect(get_db_path(project_root), timeout=0.05)
        try:
            probe.execute("BEGIN IMMEDIATE")
            observed["write_lock_released"] = True
            probe.rollback()
        finally:
            probe.close()
        raise OSError("injected unlink failure")

    monkeypatch.setattr(repository, "_delete_coordinate_file", injected_failure)

    result = repository.cleanup_expired()

    conn = get_connection(project_root)
    try:
        row = conn.execute(
            "SELECT cleanup_pending FROM target_remote_cache WHERE cache_key = ?",
            ("coordinate:fault",),
        ).fetchone()
    finally:
        conn.close()
    assert observed["write_lock_released"] is True
    assert result == {"records_deleted": 0, "files_deleted": 0}
    assert row["cleanup_pending"] == 1
    assert coordinate_file.read_text(encoding="utf-8") == "fault"


def test_cleanup_uses_cache_root_relative_paths_with_absolute_override(
    project_root, clock, monkeypatch
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir

    external_cache = project_root / "absolute-cache-override"
    monkeypatch.setenv("TARGET_CACHE_DIR", str(external_cache.resolve()))
    repository = TargetCacheRepository(project_root, clock)
    coordinate_file = get_cache_dir(project_root) / "coordinates" / "external.cif"
    coordinate_file.parent.mkdir(parents=True, exist_ok=True)
    coordinate_file.write_text("external", encoding="utf-8")
    repository.upsert(
        "coordinate:external",
        "coordinate_file",
        "RCSB_PDB",
        "external",
        {"path": "coordinates/external.cif"},
        ttl=timedelta(hours=1),
    )
    clock.value += timedelta(hours=2)

    result = repository.cleanup_expired()

    assert result == {"records_deleted": 1, "files_deleted": 1}
    assert not coordinate_file.exists()


def test_cleanup_compares_numeric_utc_expiry_not_offset_timestamp_text(
    project_root, clock
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_connection

    clock.value = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    repository = TargetCacheRepository(project_root, clock)
    repository.upsert(
        "offset:future",
        "target",
        "Provider",
        "future",
        {"value": "future"},
        ttl=timedelta(minutes=30),
    )
    conn = get_connection(project_root)
    try:
        conn.execute(
            "UPDATE target_remote_cache SET expires_at = ? WHERE cache_key = ?",
            ("2026-01-15T11:30:00-01:00", "offset:future"),
        )
        conn.commit()
    finally:
        conn.close()

    result = repository.cleanup_expired()

    conn = get_connection(project_root)
    try:
        row = conn.execute(
            "SELECT cache_key FROM target_remote_cache WHERE cache_key = ?",
            ("offset:future",),
        ).fetchone()
    finally:
        conn.close()
    assert result == {"records_deleted": 0, "files_deleted": 0}
    assert row["cache_key"] == "offset:future"


def test_cleanup_rejects_non_positive_or_non_integer_limits(project_root, clock):
    from src.target_search.cache import TargetCacheRepository

    repository = TargetCacheRepository(project_root=project_root, clock=clock)

    for limit in (0, -1, 1.5, True):
        with pytest.raises(ValueError):
            repository.cleanup_expired(limit=limit)
