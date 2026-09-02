import hashlib
import json
import multiprocessing
import os
import sqlite3
import threading
import uuid
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


class MutableClock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def _coordinate_publish_process_worker(
    project_root, now_value, installed, release_registration, finished, output
):
    from src.target_search import cache as cache_module
    from src.target_search.database import get_cache_dir

    original_upsert = getattr(cache_module.TargetCacheRepository, "_upsert_record")

    def paused_upsert(self, *args, **kwargs):
        installed.set()
        if not release_registration.wait(10):
            raise TimeoutError("registration release was not signaled")
        return original_upsert(self, *args, **kwargs)

    cache_module.TargetCacheRepository._upsert_record = paused_upsert
    try:
        cache_root = get_cache_dir(Path(project_root))
        temporary = cache_root / ".incoming" / "fresh.tmp"
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text("fresh generation", encoding="utf-8")
        evidence = cache_module.TargetCacheRepository(
            project_root=Path(project_root),
            clock=lambda: datetime.fromisoformat(now_value),
        ).publish_coordinate(
            "coordinate:publication-race",
            "RCSB_PDB",
            "fresh",
            temporary_path=".incoming/fresh.tmp",
            final_path="coordinates/fresh-generation.cif",
            ttl=timedelta(days=1),
        )
        output.put(("ok", evidence.generation_id))
    except Exception as exc:
        output.put(("error", repr(exc)))
    finally:
        finished.set()


def _plain_cleanup_process_worker(project_root, now_value, finished, output):
    from src.target_search.cache import TargetCacheRepository

    try:
        result = TargetCacheRepository(
            project_root=Path(project_root),
            clock=lambda: datetime.fromisoformat(now_value),
        ).cleanup_expired()
        output.put(("ok", result))
    except Exception as exc:
        output.put(("error", repr(exc)))
    finally:
        finished.set()


def _migration_process_worker(project_root, start, output):
    from src.target_search.database import init_db

    if not start.wait(10):
        output.put(("error", "migration start was not signaled"))
        return
    try:
        init_db(Path(project_root))
        output.put(("ok", None))
    except Exception as exc:
        output.put(("error", repr(exc)))


def _publish_coordinate(
    repository,
    project_root,
    cache_key,
    relative_path,
    *,
    source_record_id=None,
    ttl=None,
    content="coordinate",
):
    from src.target_search.database import get_cache_dir

    cache_root = get_cache_dir(project_root)
    temporary_relative = Path(".incoming") / f"{uuid.uuid4()}.tmp"
    temporary = cache_root.joinpath(*temporary_relative.parts)
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(content, encoding="utf-8")
    return repository.publish_coordinate(
        cache_key,
        "RCSB_PDB",
        source_record_id or cache_key,
        temporary_path=temporary_relative.as_posix(),
        final_path=relative_path,
        ttl=ttl,
    )


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
        cleanup_job_columns = {
            row["name"]: row
            for row in conn.execute(
                "PRAGMA table_info(target_coordinate_cleanup_jobs)"
            )
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
        "generation_id",
        "cleanup_quarantine_path",
        "next_retry_epoch",
    }.issubset(cache_columns)
    assert cache_columns["cache_key"]["pk"] == 1
    assert cache_columns["schema_version"]["dflt_value"] == "1"
    assert {"state_key", "state_value", "updated_at"}.issubset(state_columns)
    assert state_columns["state_key"]["pk"] == 1
    assert {
        "generation_id",
        "cache_key",
        "quarantine_path",
        "next_retry_epoch",
        "retry_count",
        "created_at",
    }.issubset(cleanup_job_columns)
    assert cleanup_job_columns["generation_id"]["pk"] == 1
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
            "SELECT retrieved_at, expires_at, expires_epoch, cleanup_pending, "
            "generation_id, cleanup_quarantine_path, next_retry_epoch "
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
    assert row["generation_id"]
    assert row["cleanup_quarantine_path"] is None
    assert row["next_retry_epoch"] == 0


def test_second_init_does_not_rewrite_cache_rows_after_migration_marker(
    project_root, clock, monkeypatch
):
    from src.target_search import database as database_module
    from src.target_search.cache import TargetCacheRepository

    TargetCacheRepository(project_root, clock).upsert(
        "uniprot:P00533", "target", "UniProt", "P00533", {"gene": "EGFR"}
    )
    statements = []
    real_get_connection = database_module.get_connection

    def traced_connection(root):
        connection = real_get_connection(root)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(database_module, "get_connection", traced_connection)

    database_module.init_db(project_root)

    cache_updates = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("UPDATE TARGET_REMOTE_CACHE")
    ]
    assert cache_updates == []


def test_legacy_schema_migration_is_serialized_across_spawned_processes(project_root):
    from src.target_search.database import get_db_path

    db_path = get_db_path(project_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
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
        conn.commit()
    finally:
        conn.close()

    context = multiprocessing.get_context("spawn")
    start = context.Event()
    output = context.Queue()
    processes = [
        context.Process(
            target=_migration_process_worker,
            args=(str(project_root), start, output),
        )
        for _ in range(4)
    ]
    for process in processes:
        process.start()
    start.set()
    try:
        results = [output.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(15)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)

    assert results == [("ok", None)] * len(processes)
    conn = sqlite3.connect(db_path)
    try:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(target_remote_cache)")
        }
    finally:
        conn.close()
    assert {"generation_id", "expires_epoch", "next_retry_epoch"}.issubset(columns)


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
    assert serialized["generation_id"] == target.generation_id
    assert target.generation_id != structures.generation_id
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


def test_publish_coordinate_atomically_installs_and_registers_generation(
    project_root, clock
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir

    repository = TargetCacheRepository(project_root, clock)
    cache_root = get_cache_dir(project_root)
    temporary = cache_root / ".incoming" / "4wkq.download"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text("coordinates", encoding="utf-8")
    final_file = cache_root / "coordinates" / "4wkq-generation-1.cif"
    final_file.parent.mkdir(parents=True, exist_ok=True)
    final_file.write_text("stale unregistered file", encoding="utf-8")

    evidence = repository.publish_coordinate(
        "coordinate:4WKQ",
        "RCSB_PDB",
        "4WKQ",
        temporary_path=".incoming/4wkq.download",
        final_path="coordinates/4wkq-generation-1.cif",
    )

    assert evidence.record_type == "coordinate_file"
    assert evidence.payload == {"path": "coordinates/4wkq-generation-1.cif"}
    assert final_file.read_text(encoding="utf-8") == "coordinates"
    assert not temporary.exists()
    assert repository.get("coordinate:4WKQ") == evidence


def test_generic_upsert_cannot_register_an_existing_coordinate_file(
    project_root, clock
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir

    repository = TargetCacheRepository(project_root, clock)
    final_file = get_cache_dir(project_root) / "coordinates" / "unowned.cif"
    final_file.parent.mkdir(parents=True, exist_ok=True)
    final_file.write_text("unowned", encoding="utf-8")

    with pytest.raises(ValueError, match="publish_coordinate"):
        repository.upsert(
            "coordinate:unowned",
            "coordinate_file",
            "RCSB_PDB",
            "unowned",
            {"path": "coordinates/unowned.cif"},
        )


def test_publication_rejects_reusing_an_active_generation_path(project_root, clock):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir

    repository = TargetCacheRepository(project_root, clock)
    original = _publish_coordinate(
        repository,
        project_root,
        "coordinate:immutable",
        "coordinates/immutable-generation.cif",
        content="original",
    )
    cache_root = get_cache_dir(project_root)
    temporary = cache_root / ".incoming" / "replacement.tmp"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text("replacement", encoding="utf-8")

    with pytest.raises(ValueError, match="immutable"):
        repository.publish_coordinate(
            "coordinate:immutable",
            "RCSB_PDB",
            "replacement",
            temporary_path=".incoming/replacement.tmp",
            final_path="coordinates/immutable-generation.cif",
        )

    assert repository.get("coordinate:immutable") == original
    assert temporary.read_text(encoding="utf-8") == "replacement"
    assert (
        cache_root / "coordinates" / "immutable-generation.cif"
    ).read_text(encoding="utf-8") == "original"


def test_metadata_upserts_do_not_acquire_coordinate_filesystem_lock(
    project_root, clock, monkeypatch
):
    from src.target_search import cache as cache_module

    repository = cache_module.TargetCacheRepository(project_root, clock)

    def forbidden_lock(_root):
        raise AssertionError("metadata upsert acquired coordinate lock")

    monkeypatch.setattr(cache_module, "_InterprocessCacheLock", forbidden_lock)
    evidence = repository.upsert(
        "target:metadata", "target", "UniProt", "metadata", {"gene": "EGFR"}
    )

    assert evidence.payload == {"gene": "EGFR"}


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


@pytest.mark.parametrize(
    ("record_type", "initial_payload", "corrupt_payload_json"),
    [
        ("target", {"gene": "EGFR"}, "[]"),
        ("target", {"gene": "EGFR"}, '{"score":NaN}'),
        ("structures", {"structures": []}, '{"structures":{}}'),
        ("structures", {"structures": []}, '{"other":[]}'),
        (
            "coordinate_file",
            {"path": "coordinates/valid.cif"},
            '{"path":""}',
        ),
        (
            "coordinate_file",
            {"path": "coordinates/valid.cif"},
            '{"path":"C:/outside.cif"}',
        ),
    ],
)
def test_get_strictly_rejects_non_json_constants_and_record_schema_mismatches(
    project_root, clock, record_type, initial_payload, corrupt_payload_json
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_connection

    repository = TargetCacheRepository(project_root, clock)
    if record_type == "coordinate_file":
        _publish_coordinate(
            repository,
            project_root,
            "strict:record",
            initial_payload["path"],
            source_record_id="strict",
        )
    else:
        repository.upsert(
            "strict:record", record_type, "Provider", "strict", initial_payload
        )
    digest = hashlib.sha256(corrupt_payload_json.encode("utf-8")).hexdigest()
    conn = get_connection(project_root)
    try:
        conn.execute(
            """
            UPDATE target_remote_cache
            SET payload_json = ?, payload_digest = ?
            WHERE cache_key = ?
            """,
            (corrupt_payload_json, digest, "strict:record"),
        )
        conn.commit()
    finally:
        conn.close()

    assert repository.get("strict:record", allow_stale=True) is None


@pytest.mark.parametrize("invalid_version", [True, 1.0, "1"])
def test_evidence_requires_schema_version_to_be_exact_integer(
    project_root, clock, invalid_version
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_connection

    repository = TargetCacheRepository(project_root, clock)
    repository.upsert(
        "strict:version", "target", "Provider", "strict", {"value": 1}
    )
    conn = get_connection(project_root)
    try:
        row = conn.execute(
            "SELECT * FROM target_remote_cache WHERE cache_key = ?",
            ("strict:version",),
        ).fetchone()
    finally:
        conn.close()
    row["schema_version"] = invalid_version

    assert repository._validated_evidence(row) is None


def test_expiry_epoch_validation_uses_zero_relative_tolerance(project_root, clock):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_connection

    repository = TargetCacheRepository(project_root, clock)
    repository.upsert(
        "strict:epoch", "target", "Provider", "strict", {"value": 1}
    )
    conn = get_connection(project_root)
    try:
        conn.execute(
            "UPDATE target_remote_cache SET expires_epoch = expires_epoch + 1 "
            "WHERE cache_key = ?",
            ("strict:epoch",),
        )
        conn.commit()
    finally:
        conn.close()

    assert repository.get("strict:epoch", allow_stale=True) is None


def _stored_refresh_status(project_root, repository, reason, expected_generation_id):
    from src.target_search.database import get_connection

    matched = repository.mark_refresh_failure(
        "uniprot:P00533", reason, expected_generation_id=expected_generation_id
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
        project_root, repository, reason, evidence.generation_id
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
        project_root, repository, reason, evidence.generation_id
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
        "uniprot:P00533", "target", "UniProt", "P00533", {"version": 1}
    )

    stale_matched = repository.mark_refresh_failure(
        "uniprot:P00533", "timeout", expected_generation_id=first.generation_id
    )
    current_matched = repository.mark_refresh_failure(
        "uniprot:P00533", "timeout", expected_generation_id=second.generation_id
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
    assert first.payload_digest == second.payload_digest
    assert first.generation_id != second.generation_id
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
    unrelated_file.write_text("unrelated", encoding="utf-8")
    referenced_unrelated_file.write_text("referenced unrelated", encoding="utf-8")
    outside_file.write_text("outside", encoding="utf-8")

    _publish_coordinate(
        repository,
        project_root,
        "coordinate:safe",
        "coordinates/safe.cif",
        ttl=timedelta(hours=1),
        content="safe",
    )
    _publish_coordinate(
        repository,
        project_root,
        "coordinate:outside",
        "placeholder.cif",
        ttl=timedelta(hours=1),
    )
    _publish_coordinate(
        repository,
        project_root,
        "coordinate:directory",
        "coordinate-directory.cif",
        ttl=timedelta(hours=1),
    )
    _publish_coordinate(
        repository,
        project_root,
        "coordinate:unrelated-extension",
        "referenced-unrelated.cif",
        ttl=timedelta(hours=1),
    )
    repository.upsert(
        "target:unrelated",
        "target",
        "RCSB_PDB",
        "target:unrelated",
        {"path": "unrelated.txt"},
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
        for cache_key, payload_json in (
            ("coordinate:directory", '{"path":"coordinate-directory"}'),
            (
                "coordinate:unrelated-extension",
                '{"path":"referenced-unrelated.txt"}',
            ),
        ):
            conn.execute(
                """
                UPDATE target_remote_cache
                SET payload_json = ?, payload_digest = ?
                WHERE cache_key = ?
                """,
                (
                    payload_json,
                    hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                    cache_key,
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

    assert result == {"records_deleted": 5, "files_deleted": 1}
    assert not safe_file.exists()
    assert outside_file.read_text(encoding="utf-8") == "outside"
    assert directory.is_dir()
    assert unrelated_file.read_text(encoding="utf-8") == "unrelated"
    assert referenced_unrelated_file.read_text(encoding="utf-8") == "referenced unrelated"
    assert retained == {}


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
    _publish_coordinate(
        repository,
        project_root,
        "coordinate:escape-link",
        "escape.cif",
        source_record_id="escape-link",
        ttl=timedelta(hours=1),
    )
    escape_link.unlink()
    try:
        os.symlink(outside_file, escape_link)
    except OSError as exc:
        pytest.skip(f"symlink creation unsupported: {exc}")
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
    assert result == {"records_deleted": 1, "files_deleted": 0}
    assert row is None
    assert escape_link.is_symlink()
    assert outside_file.read_text(encoding="utf-8") == "outside"


@pytest.mark.parametrize("failure_point", ["source_prevalidation", "quarantine_setup"])
def test_cleanup_transient_path_errors_retain_active_row_with_backoff(
    project_root, clock, monkeypatch, failure_point
):
    from src.target_search import cache as cache_module
    from src.target_search.database import get_cache_dir, get_connection

    repository = cache_module.TargetCacheRepository(project_root, clock)
    coordinate_file = get_cache_dir(project_root) / "coordinates" / "transient.cif"
    _publish_coordinate(
        repository,
        project_root,
        "coordinate:transient",
        "coordinates/transient.cif",
        ttl=timedelta(hours=1),
        content="transient",
    )
    clock.value += timedelta(hours=2)

    if failure_point == "source_prevalidation":
        original_prevalidate = cache_module._prevalidate_regular_file

        def transient_prevalidation(cache_root, candidate, relative_parts):
            if candidate == coordinate_file:
                return "transient_error"
            return original_prevalidate(cache_root, candidate, relative_parts)

        monkeypatch.setattr(
            cache_module, "_prevalidate_regular_file", transient_prevalidation
        )
    else:
        monkeypatch.setattr(
            cache_module,
            "_ensure_quarantine_directory",
            lambda _cache_root: "transient_error",
        )

    result = repository.cleanup_expired()

    conn = get_connection(project_root)
    try:
        active = conn.execute(
            """
            SELECT cleanup_pending, next_retry_epoch
            FROM target_remote_cache
            WHERE cache_key = ?
            """,
            ("coordinate:transient",),
        ).fetchone()
        job_count = conn.execute(
            "SELECT COUNT(*) AS count FROM target_coordinate_cleanup_jobs"
        ).fetchone()["count"]
    finally:
        conn.close()
    assert result == {"records_deleted": 0, "files_deleted": 0}
    assert active["cleanup_pending"] == 0
    assert active["next_retry_epoch"] > clock.value.timestamp()
    assert job_count == 0
    assert coordinate_file.read_text(encoding="utf-8") == "transient"


def test_cleanup_transient_quarantine_probe_retains_crash_pending_generation(
    project_root, clock, monkeypatch
):
    from src.target_search import cache as cache_module
    from src.target_search.database import get_cache_dir, get_connection

    repository = cache_module.TargetCacheRepository(project_root, clock)
    evidence = _publish_coordinate(
        repository,
        project_root,
        "coordinate:pending-transient",
        "coordinates/pending-transient.cif",
        ttl=timedelta(hours=1),
        content="pending transient",
    )
    cache_root = get_cache_dir(project_root)
    source = cache_root / "coordinates" / "pending-transient.cif"
    quarantine_relative = (
        Path(".quarantine")
        / f"{evidence.generation_id}-pending-transient.cif"
    )
    quarantine = cache_root / quarantine_relative
    quarantine.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, quarantine)
    conn = get_connection(project_root)
    try:
        conn.execute(
            """
            UPDATE target_remote_cache
            SET cleanup_pending = 1, cleanup_quarantine_path = ?
            WHERE cache_key = ? AND generation_id = ?
            """,
            (
                quarantine_relative.as_posix(),
                evidence.cache_key,
                evidence.generation_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    clock.value += timedelta(hours=2)
    original_prevalidate = cache_module._prevalidate_regular_file

    def transient_quarantine_probe(cache_root_value, candidate, relative_parts):
        if candidate == quarantine:
            return "transient_error"
        return original_prevalidate(cache_root_value, candidate, relative_parts)

    monkeypatch.setattr(
        cache_module, "_prevalidate_regular_file", transient_quarantine_probe
    )

    result = repository.cleanup_expired()

    conn = get_connection(project_root)
    try:
        active = conn.execute(
            """
            SELECT cleanup_pending, next_retry_epoch
            FROM target_remote_cache
            WHERE cache_key = ?
            """,
            (evidence.cache_key,),
        ).fetchone()
    finally:
        conn.close()
    assert result == {"records_deleted": 0, "files_deleted": 0}
    assert active["cleanup_pending"] == 1
    assert active["next_retry_epoch"] > clock.value.timestamp()
    assert quarantine.read_text(encoding="utf-8") == "pending transient"


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
    tampered_file.write_text("tampered", encoding="utf-8")
    _publish_coordinate(
        repository,
        project_root,
        "coordinate:corrupt",
        "coordinates/original.cif",
        source_record_id="corrupt",
        ttl=timedelta(hours=1),
        content="original",
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

    assert result == {"records_deleted": 1, "files_deleted": 0}
    assert retained is None
    assert original_file.read_text(encoding="utf-8") == "original"
    assert tampered_file.read_text(encoding="utf-8") == "tampered"


def test_cleanup_tombstone_commits_before_unlink_and_failed_delete_is_retriable(
    project_root, clock, monkeypatch
):
    from src.target_search import cache as cache_module
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir, get_db_path, get_connection

    repository = TargetCacheRepository(project_root, clock)
    coordinate_file = get_cache_dir(project_root) / "coordinates" / "fault.cif"
    original = _publish_coordinate(
        repository,
        project_root,
        "coordinate:fault",
        "coordinates/fault.cif",
        source_record_id="fault",
        ttl=timedelta(hours=1),
        content="fault",
    )
    clock.value += timedelta(hours=2)
    observed = {"write_lock_released": False}
    original_unlink = cache_module._unlink_quarantine_file

    def injected_failure(_cache_root, _quarantine_path):
        probe = sqlite3.connect(get_db_path(project_root), timeout=0.05)
        try:
            probe.execute("BEGIN IMMEDIATE")
            observed["write_lock_released"] = True
            probe.rollback()
        finally:
            probe.close()
        raise OSError("injected unlink failure")

    monkeypatch.setattr(
        cache_module, "_unlink_quarantine_file", injected_failure, raising=False
    )

    result = repository.cleanup_expired()

    conn = get_connection(project_root)
    try:
        active = conn.execute(
            "SELECT generation_id FROM target_remote_cache WHERE cache_key = ?",
            ("coordinate:fault",),
        ).fetchone()
        job = conn.execute(
            """
            SELECT generation_id, quarantine_path, next_retry_epoch
            FROM target_coordinate_cleanup_jobs
            WHERE generation_id = ?
            """,
            (original.generation_id,),
        ).fetchone()
    finally:
        conn.close()
    assert observed["write_lock_released"] is True
    assert result == {"records_deleted": 1, "files_deleted": 0}
    assert active is None
    assert repository.get("coordinate:fault", allow_stale=True) is None
    assert job["next_retry_epoch"] > clock.value.timestamp()
    assert job["generation_id"] in job["quarantine_path"]
    assert not coordinate_file.exists()
    quarantine_file = get_cache_dir(project_root) / job["quarantine_path"]
    assert quarantine_file.read_text(encoding="utf-8") == "fault"

    replacement = _publish_coordinate(
        repository,
        project_root,
        "coordinate:fault",
        "coordinates/fault-new-generation.cif",
        source_record_id="fault-new",
        ttl=timedelta(days=1),
        content="fresh",
    )
    assert replacement.generation_id != original.generation_id
    assert repository.get("coordinate:fault") == replacement

    monkeypatch.setattr(cache_module, "_unlink_quarantine_file", original_unlink)
    clock.value += timedelta(minutes=2)
    retry_result = repository.cleanup_expired()
    conn = get_connection(project_root)
    try:
        job_count = conn.execute(
            "SELECT COUNT(*) AS count FROM target_coordinate_cleanup_jobs"
        ).fetchone()["count"]
    finally:
        conn.close()
    assert retry_result == {"records_deleted": 0, "files_deleted": 1}
    assert job_count == 0
    assert repository.get("coordinate:fault") == replacement


def test_cleanup_retry_backoff_prevents_limit_one_starvation(
    project_root, clock, monkeypatch
):
    from src.target_search import cache as cache_module
    from src.target_search.database import get_cache_dir, get_connection

    repository = cache_module.TargetCacheRepository(project_root, clock)
    cache_dir = get_cache_dir(project_root)
    failed_file = cache_dir / "coordinates" / "a-fail.cif"
    valid_file = cache_dir / "coordinates" / "b-valid.cif"
    _publish_coordinate(
        repository,
        project_root,
        "coordinate:a-fail",
        "coordinates/a-fail.cif",
        source_record_id="a-fail",
        ttl=timedelta(hours=1),
        content="fail",
    )
    _publish_coordinate(
        repository,
        project_root,
        "coordinate:b-valid",
        "coordinates/b-valid.cif",
        source_record_id="b-valid",
        ttl=timedelta(hours=1),
        content="valid",
    )
    clock.value += timedelta(hours=2)
    original_unlink = getattr(cache_module, "_unlink_quarantine_file", None)

    def fail_first(cache_root, quarantine_path):
        if "a-fail.cif" in str(quarantine_path):
            raise OSError("transient failure")
        assert original_unlink is not None
        return original_unlink(cache_root, quarantine_path)

    monkeypatch.setattr(
        cache_module, "_unlink_quarantine_file", fail_first, raising=False
    )

    first = repository.cleanup_expired(limit=1)
    second = repository.cleanup_expired(limit=1)

    conn = get_connection(project_root)
    try:
        remaining_jobs = {
            row["cache_key"]: row["next_retry_epoch"]
            for row in conn.execute(
                "SELECT cache_key, next_retry_epoch "
                "FROM target_coordinate_cleanup_jobs"
            )
        }
        active_count = conn.execute(
            "SELECT COUNT(*) AS count FROM target_remote_cache"
        ).fetchone()["count"]
    finally:
        conn.close()
    assert first == {"records_deleted": 1, "files_deleted": 0}
    assert second == {"records_deleted": 1, "files_deleted": 1}
    assert active_count == 0
    assert set(remaining_jobs) == {"coordinate:a-fail"}
    assert remaining_jobs["coordinate:a-fail"] > clock.value.timestamp()
    assert not valid_file.exists()


def test_get_never_returns_a_coordinate_row_claimed_for_cleanup(project_root, clock):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_connection

    repository = TargetCacheRepository(project_root, clock)
    _publish_coordinate(
        repository,
        project_root,
        "coordinate:pending",
        "coordinates/pending.cif",
    )
    conn = get_connection(project_root)
    try:
        conn.execute(
            "UPDATE target_remote_cache SET cleanup_pending = 1 WHERE cache_key = ?",
            ("coordinate:pending",),
        )
        conn.commit()
    finally:
        conn.close()

    assert repository.get("coordinate:pending", allow_stale=True) is None


def test_cleanup_permanent_invalid_row_cannot_starve_later_metadata_with_limit_one(
    project_root, clock
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir, get_connection

    repository = TargetCacheRepository(project_root, clock)
    untouched_file = get_cache_dir(project_root) / "coordinates" / "invalid.cif"
    _publish_coordinate(
        repository,
        project_root,
        "coordinate:a-invalid",
        "coordinates/invalid.cif",
        source_record_id="invalid",
        ttl=timedelta(hours=1),
        content="untouched",
    )
    repository.upsert(
        "target:b-valid",
        "target",
        "UniProt",
        "valid",
        {"gene": "EGFR"},
        ttl=timedelta(hours=1),
    )
    conn = get_connection(project_root)
    try:
        conn.execute(
            "UPDATE target_remote_cache SET payload_json = ? WHERE cache_key = ?",
            ("{invalid-json", "coordinate:a-invalid"),
        )
        conn.commit()
    finally:
        conn.close()
    clock.value += timedelta(hours=2)

    first = repository.cleanup_expired(limit=1)
    second = repository.cleanup_expired(limit=1)

    conn = get_connection(project_root)
    try:
        remaining = conn.execute(
            "SELECT COUNT(*) AS count FROM target_remote_cache"
        ).fetchone()["count"]
    finally:
        conn.close()
    assert first == {"records_deleted": 1, "files_deleted": 0}
    assert second == {"records_deleted": 1, "files_deleted": 0}
    assert remaining == 0
    assert untouched_file.read_text(encoding="utf-8") == "untouched"


def test_cleanup_uses_cache_root_relative_paths_with_absolute_override(
    project_root, clock, monkeypatch
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir

    external_cache = project_root / "absolute-cache-override"
    monkeypatch.setenv("TARGET_CACHE_DIR", str(external_cache.resolve()))
    repository = TargetCacheRepository(project_root, clock)
    coordinate_file = get_cache_dir(project_root) / "coordinates" / "external.cif"
    _publish_coordinate(
        repository,
        project_root,
        "coordinate:external",
        "coordinates/external.cif",
        source_record_id="external",
        ttl=timedelta(hours=1),
        content="external",
    )
    clock.value += timedelta(hours=2)

    result = repository.cleanup_expired()

    assert result == {"records_deleted": 1, "files_deleted": 1}
    assert not coordinate_file.exists()


def test_coordinate_publication_blocks_cleanup_until_fresh_generation_is_registered(
    project_root, clock
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir

    repository = TargetCacheRepository(project_root, clock)
    old = _publish_coordinate(
        repository,
        project_root,
        "coordinate:publication-race",
        "coordinates/old-generation.cif",
        source_record_id="old",
        ttl=timedelta(hours=1),
        content="old generation",
    )
    clock.value += timedelta(hours=2)

    context = multiprocessing.get_context("spawn")
    installed = context.Event()
    release_registration = context.Event()
    publication_finished = context.Event()
    cleanup_finished = context.Event()
    publication_output = context.Queue()
    cleanup_output = context.Queue()
    publication_process = context.Process(
        target=_coordinate_publish_process_worker,
        args=(
            str(project_root),
            clock.value.isoformat(),
            installed,
            release_registration,
            publication_finished,
            publication_output,
        ),
    )
    cleanup_process = context.Process(
        target=_plain_cleanup_process_worker,
        args=(
            str(project_root),
            clock.value.isoformat(),
            cleanup_finished,
            cleanup_output,
        ),
    )
    publication_process.start()
    try:
        assert installed.wait(10), "publication never installed the fresh file"
        cleanup_process.start()
        assert not cleanup_finished.wait(0.3), "cleanup bypassed publication lock"
        release_registration.set()
        publication_process.join(10)
        cleanup_process.join(10)
        assert publication_process.exitcode == 0
        assert cleanup_process.exitcode == 0
        publication_status, fresh_generation = publication_output.get(timeout=5)
        cleanup_status, cleanup_result = cleanup_output.get(timeout=5)
        assert publication_status == "ok", fresh_generation
        assert cleanup_status == "ok", cleanup_result
        assert cleanup_result == {"records_deleted": 0, "files_deleted": 0}
    finally:
        release_registration.set()
        for process in (publication_process, cleanup_process):
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join(5)

    fresh_file = get_cache_dir(project_root) / "coordinates" / "fresh-generation.cif"
    current = repository.get("coordinate:publication-race", allow_stale=True)
    assert current is not None
    assert current.generation_id == fresh_generation
    assert current.generation_id != old.generation_id
    assert current.payload == {"path": "coordinates/fresh-generation.cif"}
    assert fresh_file.read_text(encoding="utf-8") == "fresh generation"
    assert not list((get_cache_dir(project_root) / ".quarantine").glob("*"))


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
