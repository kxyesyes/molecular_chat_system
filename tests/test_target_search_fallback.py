import hashlib
import os
import tempfile
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
def project_root():
    with tempfile.TemporaryDirectory(prefix="target_cache_test_") as directory:
        yield Path(directory)


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

    assert list(cache_columns) == [
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
    ]
    assert cache_columns["cache_key"]["pk"] == 1
    assert cache_columns["schema_version"]["dflt_value"] == "1"
    assert list(state_columns) == ["state_key", "state_value", "updated_at"]
    assert state_columns["state_key"]["pk"] == 1
    assert "idx_target_remote_cache_expires_at" in indexes


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


def test_mark_refresh_failure_stores_only_bounded_redacted_single_line_status(
    project_root, clock
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
    secret = "sk-secret-value-that-must-not-be-stored"
    reason = (
        f"HTTP 503\nAuthorization: Bearer {secret}\n"
        f"response body={{\"token\":\"{secret}\",\"data\":\"{'x' * 800}\"}}"
    )

    repository.mark_refresh_failure("uniprot:P00533", reason)

    conn = get_connection(project_root)
    try:
        row = conn.execute(
            "SELECT last_refresh_status FROM target_remote_cache WHERE cache_key = ?",
            ("uniprot:P00533",),
        ).fetchone()
    finally:
        conn.close()

    status = row["last_refresh_status"]
    assert len(status) <= 500
    assert "\n" not in status and "\r" not in status
    assert secret not in status
    assert "Bearer" not in status
    assert '"data"' not in status
    assert "response body redacted" in status.lower()


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
    from src.target_search.database import get_cache_dir

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
            {"path": "data/target_db/cache/coordinates/safe.cif"},
        ),
        ("coordinate:outside", "coordinate_file", {"path": "outside.cif"}),
        (
            "coordinate:directory",
            "coordinate_file",
            {"path": "data/target_db/cache/coordinate-directory"},
        ),
        (
            "target:unrelated",
            "target",
            {"path": "data/target_db/cache/unrelated.txt"},
        ),
        (
            "coordinate:unrelated-extension",
            "coordinate_file",
            {"path": "data/target_db/cache/referenced-unrelated.txt"},
        ),
    ]

    escape_link = cache_dir / "escape.cif"
    try:
        os.symlink(outside_file, escape_link)
    except OSError:
        escape_link = None
    else:
        entries.append(
            (
                "coordinate:escape-link",
                "coordinate_file",
                {"path": "data/target_db/cache/escape.cif"},
            )
        )

    for cache_key, record_type, payload in entries:
        repository.upsert(
            cache_key,
            record_type,
            "RCSB_PDB",
            cache_key,
            payload,
            ttl=timedelta(hours=1),
        )
    clock.value += timedelta(hours=2)

    result = repository.cleanup_expired(limit=100)

    assert result == {"records_deleted": len(entries), "files_deleted": 1}
    assert not safe_file.exists()
    assert outside_file.read_text(encoding="utf-8") == "outside"
    assert directory.is_dir()
    assert unrelated_file.read_text(encoding="utf-8") == "unrelated"
    assert referenced_unrelated_file.read_text(encoding="utf-8") == "referenced unrelated"
    if escape_link is not None:
        assert escape_link.is_symlink()


@pytest.mark.parametrize(
    ("column", "corrupt_value"),
    [
        (
            "payload_json",
            '{"path":"data/target_db/cache/coordinates/tampered.cif"}',
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
        {"path": "data/target_db/cache/coordinates/original.cif"},
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

    assert result == {"records_deleted": 1, "files_deleted": 0}
    assert original_file.read_text(encoding="utf-8") == "original"
    assert tampered_file.read_text(encoding="utf-8") == "tampered"


def test_cleanup_rejects_non_positive_or_non_integer_limits(project_root, clock):
    from src.target_search.cache import TargetCacheRepository

    repository = TargetCacheRepository(project_root=project_root, clock=clock)

    for limit in (0, -1, 1.5, True):
        with pytest.raises(ValueError):
            repository.cleanup_expired(limit=limit)
