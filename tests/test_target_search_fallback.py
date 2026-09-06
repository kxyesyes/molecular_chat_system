import gzip
import hashlib
import json
import multiprocessing
import os
import pickle
import sqlite3
import threading
import time
import uuid
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests


@pytest.fixture(autouse=True)
def _block_unexpected_http(monkeypatch, request):
    if request.node.get_closest_marker("real_external") is not None:
        return

    def blocked_send(self, request, **kwargs):
        raise AssertionError("unit tests must use an explicit fake adapter")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", blocked_send)


@pytest.mark.real_external
def test_real_pde5a_authoritative_lookup(tmp_path, monkeypatch):
    if os.getenv("RUN_REAL_TARGET_SEARCH") != "1":
        pytest.skip("real authoritative target lookup not enabled")

    from src.target_search.service import TargetSearchService

    monkeypatch.setenv("TARGET_DB_PATH", str(tmp_path / "runtime" / "targets.sqlite"))
    monkeypatch.setenv("TARGET_CACHE_DIR", str(tmp_path / "runtime" / "cache"))
    with TargetSearchService(tmp_path, auto_seed=False) as service:
        result = service.search_targets("PDE5A")

    assert result["status"] == "resolved", result
    assert result["results"], result
    target = result["results"][0]
    assert target["uniprot_id"] == "O76074"
    assert target["source"] == "UniProt"
    assert target["recommended_structures"]
    assert target["recommended_structures"][0]["source"] in {
        "RCSB_PDB",
        "AlphaFold",
    }


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

    original_activate = getattr(
        cache_module.TargetCacheRepository, "_activate_publication_intent"
    )

    def paused_activation(self, *args, **kwargs):
        installed.set()
        if not release_registration.wait(10):
            raise TimeoutError("registration release was not signaled")
        return original_activate(self, *args, **kwargs)

    cache_module.TargetCacheRepository._activate_publication_intent = paused_activation
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
            staged_path=".incoming/fresh.tmp",
            coordinate_suffix=".cif",
            ttl=timedelta(days=1),
        )
        output.put(("ok", evidence.to_dict()))
    except Exception as exc:
        output.put(("error", repr(exc)))
    finally:
        finished.set()


def _crash_after_coordinate_install_worker(project_root, now_value, output):
    from src.target_search import cache as cache_module
    from src.target_search.database import get_cache_dir

    def crash_before_activation(self, intent_id):
        output.put(("installed", intent_id))
        output.close()
        output.join_thread()
        os._exit(73)

    cache_module.TargetCacheRepository._activate_publication_intent = (
        crash_before_activation
    )
    cache_root = get_cache_dir(Path(project_root))
    staged = cache_root / ".incoming" / "crash.cif.part"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text("crash generation", encoding="utf-8")
    cache_module.TargetCacheRepository(
        project_root=Path(project_root),
        clock=lambda: datetime.fromisoformat(now_value),
    ).publish_coordinate(
        "coordinate:crash",
        "RCSB_PDB",
        "crash-record",
        staged_path=".incoming/crash.cif.part",
        coordinate_suffix=".cif",
    )


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


def _auto_seed_process_worker(project_root, start, first_count_barrier, call_log, output):
    from src.target_search import service as service_module
    from src.target_search.seed import seed_database as real_seed_database

    class RemoteMustNotRun:
        def resolve(self, query, organism):
            raise AssertionError("authoritative resolver must not run after seed")

    def counted_seed(root):
        with Path(call_log).open("a", encoding="utf-8") as stream:
            stream.write(f"{os.getpid()}\n")
        time.sleep(0.1)
        return real_seed_database(root)

    try:
        service = service_module.TargetSearchService(
            project_root=Path(project_root),
            resolver=RemoteMustNotRun(),
            auto_seed=True,
        )
        original_target_count = service._target_count
        first_count = True

        def synchronized_target_count():
            nonlocal first_count
            count = original_target_count()
            if first_count:
                first_count = False
                first_count_barrier.wait(timeout=10)
            return count

        service._target_count = synchronized_target_count
        service_module.seed_database = counted_seed
        if not start.wait(10):
            output.put(("error", "seed start was not signaled"))
            return
        output.put(("ok", service.search_targets("EGFR")["lookup_path"]))
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
    suffix = Path(relative_path).suffix.lower()
    return repository.publish_coordinate(
        cache_key,
        "RCSB_PDB",
        source_record_id or cache_key,
        staged_path=temporary_relative.as_posix(),
        coordinate_suffix=suffix,
        ttl=ttl,
    )


def _insert_corrupt_publication_intent(
    project_root,
    clock,
    cache_key,
    *,
    staged_path=".incoming/corrupt.tmp",
    previous=None,
    intent_id=None,
):
    from src.target_search.database import get_connection

    intent_id = str(uuid.uuid4()) if intent_id is None else intent_id
    generation_id = str(uuid.uuid4())
    retrieved_at = clock.value
    expires_at = retrieved_at + timedelta(days=1)
    previous_generation_id = previous.generation_id if previous is not None else None
    previous_path = previous.payload["path"] if previous is not None else None
    previous_quarantine_path = (
        f".quarantine/{previous.generation_id}-{Path(previous_path).name}"
        if previous is not None
        else None
    )
    conn = get_connection(project_root)
    try:
        conn.execute(
            """
            INSERT INTO target_coordinate_publication_intents (
                intent_id, generation_id, cache_key, staged_path, final_path,
                source, source_record_id, status, retrieved_at, expires_at,
                expires_epoch, previous_generation_id, previous_path,
                previous_quarantine_path, created_at, updated_at
            ) VALUES (?, ?, ?, ?, '../outside.cif', 'RCSB_PDB', 'corrupt',
                'reserved', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent_id,
                generation_id,
                cache_key,
                staged_path,
                retrieved_at.isoformat(),
                expires_at.isoformat(),
                expires_at.timestamp(),
                previous_generation_id,
                previous_path,
                previous_quarantine_path,
                retrieved_at.isoformat(),
                retrieved_at.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return intent_id


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
        publication_intent_columns = {
            row["name"]: row
            for row in conn.execute(
                "PRAGMA table_info(target_coordinate_publication_intents)"
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
    assert {
        "intent_id",
        "generation_id",
        "cache_key",
        "staged_path",
        "final_path",
        "source",
        "source_record_id",
        "status",
        "retrieved_at",
        "expires_at",
        "created_at",
        "updated_at",
    }.issubset(publication_intent_columns)
    assert publication_intent_columns["intent_id"]["pk"] == 1
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

    evidence = repository.publish_coordinate(
        "coordinate:4WKQ",
        "RCSB_PDB",
        "4WKQ",
        staged_path=".incoming/4wkq.download",
        coordinate_suffix=".cif",
    )

    final_relative = Path(evidence.payload["path"])
    final_file = cache_root.joinpath(*final_relative.parts)
    assert evidence.record_type == "coordinate_file"
    assert final_relative.parts[0] == "coordinates"
    assert evidence.generation_id in final_relative.name
    assert final_relative.suffix == ".cif"
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


def test_publication_rejects_an_active_coordinate_as_its_staged_source(
    project_root, clock
):
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
    original_file = cache_root / original.payload["path"]

    with pytest.raises(ValueError, match="incoming"):
        repository.publish_coordinate(
            "coordinate:immutable",
            "RCSB_PDB",
            "replacement",
            staged_path=original.payload["path"],
            coordinate_suffix=".cif",
        )

    assert repository.get("coordinate:immutable") == original
    assert original_file.read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize(
    "staged_path",
    [
        "coordinates/source.cif",
        ".locks/source.cif",
        ".quarantine/source.cif",
        ".incoming/.locks/source.cif",
        ".incoming/.quarantine/source.cif",
    ],
)
def test_coordinate_publication_accepts_staged_files_only_from_incoming(
    project_root, clock, staged_path
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir

    repository = TargetCacheRepository(project_root, clock)
    candidate = get_cache_dir(project_root) / staged_path
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("must remain", encoding="utf-8")

    with pytest.raises(ValueError, match="incoming|internal"):
        repository.publish_coordinate(
            "coordinate:namespace",
            "RCSB_PDB",
            "namespace",
            staged_path=staged_path,
            coordinate_suffix=".cif",
        )

    assert candidate.read_text(encoding="utf-8") == "must remain"
    assert repository.get("coordinate:namespace", allow_stale=True) is None


def test_coordinate_publication_never_replaces_an_untracked_destination(
    project_root, clock, monkeypatch
):
    from src.target_search import cache as cache_module
    from src.target_search.database import get_cache_dir

    repository = cache_module.TargetCacheRepository(project_root, clock)
    fixed_generation = uuid.UUID("00000000-0000-4000-8000-000000000123")
    monkeypatch.setattr(cache_module.uuid, "uuid4", lambda: fixed_generation)
    final_relative = cache_module._derive_coordinate_final_path(
        "RCSB_PDB", "occupied", str(fixed_generation), ".cif"
    )
    cache_root = get_cache_dir(project_root)
    destination = cache_root / final_relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("untracked owner", encoding="utf-8")
    staged = cache_root / ".incoming" / "occupied.tmp"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text("candidate", encoding="utf-8")

    with pytest.raises((FileExistsError, ValueError), match="exist|destination"):
        repository.publish_coordinate(
            "coordinate:occupied",
            "RCSB_PDB",
            "occupied",
            staged_path=".incoming/occupied.tmp",
            coordinate_suffix=".cif",
        )

    assert destination.read_text(encoding="utf-8") == "untracked owner"
    assert staged.read_text(encoding="utf-8") == "candidate"
    assert repository.get("coordinate:occupied", allow_stale=True) is None


def test_same_key_coordinate_republish_retires_old_generation_without_orphan(
    project_root, clock
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir, get_connection

    repository = TargetCacheRepository(project_root, clock)
    first = _publish_coordinate(
        repository,
        project_root,
        "coordinate:republish",
        "first.cif",
        source_record_id="first",
        content="first generation",
    )
    first_file = get_cache_dir(project_root) / first.payload["path"]

    second = _publish_coordinate(
        repository,
        project_root,
        "coordinate:republish",
        "second.cif",
        source_record_id="second",
        content="second generation",
    )

    conn = get_connection(project_root)
    try:
        job = conn.execute(
            "SELECT * FROM target_coordinate_cleanup_jobs WHERE generation_id = ?",
            (first.generation_id,),
        ).fetchone()
        intent_count = conn.execute(
            "SELECT COUNT(*) AS count "
            "FROM target_coordinate_publication_intents"
        ).fetchone()["count"]
    finally:
        conn.close()
    second_file = get_cache_dir(project_root) / second.payload["path"]
    quarantine = get_cache_dir(project_root) / job["quarantine_path"]
    assert repository.get("coordinate:republish") == second
    assert second.generation_id != first.generation_id
    assert second_file.read_text(encoding="utf-8") == "second generation"
    assert not first_file.exists()
    assert quarantine.read_text(encoding="utf-8") == "first generation"
    assert intent_count == 0


def test_same_key_republish_retires_missing_prior_file_without_stuck_intent(
    project_root, clock
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir, get_connection

    repository = TargetCacheRepository(project_root, clock)
    first = _publish_coordinate(
        repository,
        project_root,
        "coordinate:missing-prior",
        "first.cif",
        source_record_id="first",
        content="first generation",
    )
    first_file = get_cache_dir(project_root) / first.payload["path"]
    first_file.unlink()

    second = _publish_coordinate(
        repository,
        project_root,
        "coordinate:missing-prior",
        "second.cif",
        source_record_id="second",
        content="second generation",
    )

    conn = get_connection(project_root)
    try:
        intents = conn.execute(
            "SELECT COUNT(*) AS count FROM target_coordinate_publication_intents"
        ).fetchone()["count"]
        stale_jobs = conn.execute(
            "SELECT COUNT(*) AS count FROM target_coordinate_cleanup_jobs "
            "WHERE generation_id = ?",
            (first.generation_id,),
        ).fetchone()["count"]
        active = conn.execute(
            "SELECT generation_id, cleanup_pending FROM target_remote_cache "
            "WHERE cache_key = ?",
            ("coordinate:missing-prior",),
        ).fetchone()
    finally:
        conn.close()
    second_file = get_cache_dir(project_root) / second.payload["path"]
    assert repository.get("coordinate:missing-prior") == second
    assert second_file.read_text(encoding="utf-8") == "second generation"
    assert intents == 0
    assert stale_jobs == 0
    assert active == {"generation_id": second.generation_id, "cleanup_pending": 0}


def test_initialization_discards_corrupt_intent_without_touching_its_paths(
    project_root, clock
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir, get_connection

    TargetCacheRepository(project_root, clock)
    cache_root = get_cache_dir(project_root)
    staged = cache_root / ".incoming" / "corrupt.tmp"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text("untrusted staged file", encoding="utf-8")
    outside = project_root / "outside.cif"
    outside.write_text("outside sentinel", encoding="utf-8")
    intent_id = _insert_corrupt_publication_intent(
        project_root, clock, "coordinate:corrupt-init"
    )

    repository = TargetCacheRepository(project_root, clock)

    conn = get_connection(project_root)
    try:
        intent = conn.execute(
            "SELECT * FROM target_coordinate_publication_intents "
            "WHERE intent_id = ?",
            (intent_id,),
        ).fetchone()
    finally:
        conn.close()
    reused = repository.upsert(
        "coordinate:corrupt-init",
        "target",
        "UniProt",
        "P00533",
        {"gene": "EGFR"},
    )
    assert intent is None
    assert reused.record_type == "target"
    assert staged.read_text(encoding="utf-8") == "untrusted staged file"
    assert outside.read_text(encoding="utf-8") == "outside sentinel"


@pytest.mark.parametrize("reconcile_mode", ["initialization", "cleanup"])
def test_reconciliation_deletes_blob_intent_id_by_trusted_rowid_and_reuses_key(
    project_root, clock, reconcile_mode
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_connection

    repository = TargetCacheRepository(project_root, clock)
    cache_key = f"coordinate:blob-intent:{reconcile_mode}"
    _insert_corrupt_publication_intent(
        project_root,
        clock,
        cache_key,
        intent_id=sqlite3.Binary(f"blob-{reconcile_mode}".encode("utf-8")),
    )

    if reconcile_mode == "initialization":
        repository = TargetCacheRepository(project_root, clock)
    else:
        assert repository.cleanup_expired() == {
            "records_deleted": 0,
            "files_deleted": 0,
        }

    conn = get_connection(project_root)
    try:
        remaining = conn.execute(
            "SELECT COUNT(*) AS count FROM target_coordinate_publication_intents "
            "WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()["count"]
    finally:
        conn.close()
    reused = repository.upsert(
        cache_key,
        "target",
        "UniProt",
        "P00533",
        {"gene": "EGFR"},
    )
    assert remaining == 0
    assert reused.cache_key == cache_key


def test_cleanup_discards_corrupt_intent_and_preserves_valid_prior_generation(
    project_root, clock
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir, get_connection

    repository = TargetCacheRepository(project_root, clock)
    first = _publish_coordinate(
        repository,
        project_root,
        "coordinate:corrupt-cleanup",
        "first.cif",
        source_record_id="first",
        content="first generation",
    )
    first_file = get_cache_dir(project_root) / first.payload["path"]
    outside = project_root / "outside.cif"
    outside.write_text("outside sentinel", encoding="utf-8")
    intent_id = _insert_corrupt_publication_intent(
        project_root,
        clock,
        "coordinate:corrupt-cleanup",
        previous=first,
    )
    previous_quarantine_path = (
        f".quarantine/{first.generation_id}-{Path(first.payload['path']).name}"
    )
    conn = get_connection(project_root)
    try:
        conn.execute(
            """
            UPDATE target_remote_cache
            SET cleanup_pending = 1, cleanup_quarantine_path = ?
            WHERE cache_key = ? AND generation_id = ?
            """,
            (
                previous_quarantine_path,
                first.cache_key,
                first.generation_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    cleanup_result = repository.cleanup_expired()

    conn = get_connection(project_root)
    try:
        intent = conn.execute(
            "SELECT * FROM target_coordinate_publication_intents "
            "WHERE intent_id = ?",
            (intent_id,),
        ).fetchone()
    finally:
        conn.close()
    assert cleanup_result == {"records_deleted": 0, "files_deleted": 0}
    assert intent is None
    assert repository.get("coordinate:corrupt-cleanup") == first
    assert first_file.read_text(encoding="utf-8") == "first generation"
    assert outside.read_text(encoding="utf-8") == "outside sentinel"

    second = _publish_coordinate(
        repository,
        project_root,
        "coordinate:corrupt-cleanup",
        "second.cif",
        source_record_id="second",
        content="second generation",
    )
    assert repository.get("coordinate:corrupt-cleanup") == second


def test_corrupt_intent_transient_prior_probe_retains_linked_active_generation(
    project_root, clock, monkeypatch
):
    from src.target_search import cache as cache_module
    from src.target_search.database import get_cache_dir, get_connection

    repository = cache_module.TargetCacheRepository(project_root, clock)
    first = _publish_coordinate(
        repository,
        project_root,
        "coordinate:corrupt-transient",
        "first.cif",
        source_record_id="first",
        content="first generation",
    )
    cache_root = get_cache_dir(project_root)
    first_file = cache_root / first.payload["path"]
    quarantine_path = (
        f".quarantine/{first.generation_id}-{Path(first.payload['path']).name}"
    )
    _insert_corrupt_publication_intent(
        project_root,
        clock,
        first.cache_key,
        previous=first,
    )
    conn = get_connection(project_root)
    try:
        conn.execute(
            """
            UPDATE target_remote_cache
            SET cleanup_pending = 1, cleanup_quarantine_path = ?
            WHERE cache_key = ? AND generation_id = ?
            """,
            (quarantine_path, first.cache_key, first.generation_id),
        )
        conn.commit()
    finally:
        conn.close()
    original_prevalidate = cache_module._prevalidate_regular_file

    def transient_source_probe(cache_root_value, candidate, relative_parts):
        if candidate == first_file:
            return "transient_error"
        return original_prevalidate(cache_root_value, candidate, relative_parts)

    monkeypatch.setattr(
        cache_module, "_prevalidate_regular_file", transient_source_probe
    )

    result = repository.cleanup_expired()

    conn = get_connection(project_root)
    try:
        active = conn.execute(
            "SELECT generation_id, cleanup_pending FROM target_remote_cache "
            "WHERE cache_key = ?",
            (first.cache_key,),
        ).fetchone()
        intents = conn.execute(
            "SELECT COUNT(*) AS count FROM target_coordinate_publication_intents "
            "WHERE cache_key = ?",
            (first.cache_key,),
        ).fetchone()["count"]
    finally:
        conn.close()
    assert result == {"records_deleted": 0, "files_deleted": 0}
    assert active == {"generation_id": first.generation_id, "cleanup_pending": 0}
    assert intents == 0
    assert repository.get(first.cache_key) == first
    assert first_file.read_text(encoding="utf-8") == "first generation"

    monkeypatch.setattr(
        cache_module, "_prevalidate_regular_file", original_prevalidate
    )
    second = _publish_coordinate(
        repository,
        project_root,
        first.cache_key,
        "second.cif",
        source_record_id="second",
        content="second generation",
    )
    assert repository.get(first.cache_key) == second


def test_ambiguous_quarantined_prior_retries_then_deletes_without_orphan_or_lock(
    project_root, clock, monkeypatch
):
    from src.target_search import cache as cache_module
    from src.target_search.database import get_cache_dir, get_connection

    repository = cache_module.TargetCacheRepository(project_root, clock)
    first = _publish_coordinate(
        repository,
        project_root,
        "coordinate:ambiguous-prior",
        "first.cif",
        source_record_id="first",
        content="first generation",
    )
    cache_root = get_cache_dir(project_root)
    source = cache_root / first.payload["path"]
    quarantine_relative = (
        Path(".quarantine")
        / f"{first.generation_id}-{Path(first.payload['path']).name}"
    )
    quarantine = cache_root / quarantine_relative
    quarantine.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, quarantine)
    _insert_corrupt_publication_intent(
        project_root,
        clock,
        first.cache_key,
        previous=first,
    )
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
                first.cache_key,
                first.generation_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    original_prevalidate = cache_module._prevalidate_regular_file

    def transient_source_probe(cache_root_value, candidate, relative_parts):
        if candidate == source:
            return "transient_error"
        return original_prevalidate(cache_root_value, candidate, relative_parts)

    monkeypatch.setattr(
        cache_module, "_prevalidate_regular_file", transient_source_probe
    )

    first_cleanup = repository.cleanup_expired()

    conn = get_connection(project_root)
    try:
        pending = conn.execute(
            """
            SELECT generation_id, cleanup_pending, cleanup_quarantine_path,
                next_retry_epoch
            FROM target_remote_cache WHERE cache_key = ?
            """,
            (first.cache_key,),
        ).fetchone()
        intents = conn.execute(
            "SELECT COUNT(*) AS count FROM target_coordinate_publication_intents "
            "WHERE cache_key = ?",
            (first.cache_key,),
        ).fetchone()["count"]
    finally:
        conn.close()
    assert first_cleanup == {"records_deleted": 0, "files_deleted": 0}
    assert pending["generation_id"] == first.generation_id
    assert pending["cleanup_pending"] == 1
    assert pending["cleanup_quarantine_path"] == quarantine_relative.as_posix()
    assert pending["next_retry_epoch"] > clock.value.timestamp()
    assert intents == 0
    assert not source.exists()
    assert quarantine.read_text(encoding="utf-8") == "first generation"

    monkeypatch.setattr(
        cache_module, "_prevalidate_regular_file", original_prevalidate
    )
    clock.value += timedelta(minutes=2)
    second_cleanup = repository.cleanup_expired()

    conn = get_connection(project_root)
    try:
        active = conn.execute(
            "SELECT * FROM target_remote_cache WHERE cache_key = ?",
            (first.cache_key,),
        ).fetchone()
        jobs = conn.execute(
            "SELECT COUNT(*) AS count FROM target_coordinate_cleanup_jobs "
            "WHERE generation_id = ?",
            (first.generation_id,),
        ).fetchone()["count"]
    finally:
        conn.close()
    assert second_cleanup == {"records_deleted": 1, "files_deleted": 1}
    assert active is None
    assert jobs == 0
    assert not source.exists()
    assert not quarantine.exists()

    replacement = _publish_coordinate(
        repository,
        project_root,
        first.cache_key,
        "replacement.cif",
        source_record_id="replacement",
        content="replacement generation",
    )
    assert repository.get(first.cache_key) == replacement


def test_generic_metadata_cannot_overwrite_coordinate_owned_key(project_root, clock):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir

    repository = TargetCacheRepository(project_root, clock)
    coordinate = _publish_coordinate(
        repository,
        project_root,
        "shared:key",
        "owned.cif",
        content="owned coordinate",
    )
    coordinate_file = get_cache_dir(project_root) / coordinate.payload["path"]

    with pytest.raises(ValueError, match="coordinate"):
        repository.upsert(
            "shared:key", "target", "UniProt", "P00533", {"gene": "EGFR"}
        )

    assert repository.get("shared:key") == coordinate
    assert coordinate_file.read_text(encoding="utf-8") == "owned coordinate"


def test_coordinate_publication_rejects_cross_type_key_conflict(project_root, clock):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir

    repository = TargetCacheRepository(project_root, clock)
    metadata = repository.upsert(
        "shared:key", "target", "UniProt", "P00533", {"gene": "EGFR"}
    )
    staged = get_cache_dir(project_root) / ".incoming" / "conflict.tmp"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text("coordinate", encoding="utf-8")

    with pytest.raises(ValueError, match="record type|cross-type|coordinate"):
        repository.publish_coordinate(
            "shared:key",
            "RCSB_PDB",
            "4WKQ",
            staged_path=".incoming/conflict.tmp",
            coordinate_suffix=".cif",
        )

    assert repository.get("shared:key") == metadata
    assert staged.read_text(encoding="utf-8") == "coordinate"


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
    unrelated_file = cache_dir / "unrelated.txt"
    referenced_unrelated_file = cache_dir / "referenced-unrelated.txt"
    directory = cache_dir / "coordinate-directory"
    outside_file = project_root / "outside.cif"
    directory.mkdir(parents=True, exist_ok=True)
    unrelated_file.write_text("unrelated", encoding="utf-8")
    referenced_unrelated_file.write_text("referenced unrelated", encoding="utf-8")
    outside_file.write_text("outside", encoding="utf-8")

    safe_evidence = _publish_coordinate(
        repository,
        project_root,
        "coordinate:safe",
        "coordinates/safe.cif",
        ttl=timedelta(hours=1),
        content="safe",
    )
    safe_file = cache_dir / safe_evidence.payload["path"]
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
    cache_dir.mkdir(parents=True, exist_ok=True)
    outside_file.write_text("outside", encoding="utf-8")
    evidence = _publish_coordinate(
        repository,
        project_root,
        "coordinate:escape-link",
        "escape.cif",
        source_record_id="escape-link",
        ttl=timedelta(hours=1),
    )
    escape_link = cache_dir / evidence.payload["path"]
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
    evidence = _publish_coordinate(
        repository,
        project_root,
        "coordinate:transient",
        "coordinates/transient.cif",
        ttl=timedelta(hours=1),
        content="transient",
    )
    coordinate_file = get_cache_dir(project_root) / evidence.payload["path"]
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
    source = cache_root / evidence.payload["path"]
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
    tampered_file = coordinate_dir / "tampered.cif"
    coordinate_dir.mkdir(parents=True, exist_ok=True)
    tampered_file.write_text("tampered", encoding="utf-8")
    evidence = _publish_coordinate(
        repository,
        project_root,
        "coordinate:corrupt",
        "coordinates/original.cif",
        source_record_id="corrupt",
        ttl=timedelta(hours=1),
        content="original",
    )
    original_file = get_cache_dir(project_root) / evidence.payload["path"]
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
    original = _publish_coordinate(
        repository,
        project_root,
        "coordinate:fault",
        "coordinates/fault.cif",
        source_record_id="fault",
        ttl=timedelta(hours=1),
        content="fault",
    )
    coordinate_file = get_cache_dir(project_root) / original.payload["path"]
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
    failed = _publish_coordinate(
        repository,
        project_root,
        "coordinate:a-fail",
        "coordinates/a-fail.cif",
        source_record_id="a-fail",
        ttl=timedelta(hours=1),
        content="fail",
    )
    valid = _publish_coordinate(
        repository,
        project_root,
        "coordinate:b-valid",
        "coordinates/b-valid.cif",
        source_record_id="b-valid",
        ttl=timedelta(hours=1),
        content="valid",
    )
    failed_file = cache_dir / failed.payload["path"]
    valid_file = cache_dir / valid.payload["path"]
    clock.value += timedelta(hours=2)
    original_unlink = getattr(cache_module, "_unlink_quarantine_file", None)

    def fail_first(cache_root, quarantine_path):
        if failed.generation_id in str(quarantine_path):
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
    invalid = _publish_coordinate(
        repository,
        project_root,
        "coordinate:a-invalid",
        "coordinates/invalid.cif",
        source_record_id="invalid",
        ttl=timedelta(hours=1),
        content="untouched",
    )
    untouched_file = get_cache_dir(project_root) / invalid.payload["path"]
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
    evidence = _publish_coordinate(
        repository,
        project_root,
        "coordinate:external",
        "coordinates/external.cif",
        source_record_id="external",
        ttl=timedelta(hours=1),
        content="external",
    )
    coordinate_file = get_cache_dir(project_root) / evidence.payload["path"]
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
        publication_status, fresh = publication_output.get(timeout=5)
        cleanup_status, cleanup_result = cleanup_output.get(timeout=5)
        assert publication_status == "ok", fresh
        assert cleanup_status == "ok", cleanup_result
        assert cleanup_result == {"records_deleted": 0, "files_deleted": 1}
    finally:
        release_registration.set()
        for process in (publication_process, cleanup_process):
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join(5)

    fresh_file = get_cache_dir(project_root) / fresh["payload"]["path"]
    current = repository.get("coordinate:publication-race", allow_stale=True)
    assert current is not None
    assert current.generation_id == fresh["generation_id"]
    assert current.generation_id != old.generation_id
    assert current.payload == fresh["payload"]
    assert fresh_file.read_text(encoding="utf-8") == "fresh generation"
    assert not list((get_cache_dir(project_root) / ".quarantine").glob("*"))


def test_repository_recovers_publication_after_process_crashes_before_activation(
    project_root, clock
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir, get_connection

    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    process = context.Process(
        target=_crash_after_coordinate_install_worker,
        args=(str(project_root), clock.value.isoformat(), output),
    )
    process.start()
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail("publication crash worker did not terminate")
    assert process.exitcode == 73
    status, intent_id = output.get(timeout=5)
    assert status == "installed"

    conn = get_connection(project_root)
    try:
        intent = conn.execute(
            "SELECT * FROM target_coordinate_publication_intents "
            "WHERE intent_id = ?",
            (intent_id,),
        ).fetchone()
        active = conn.execute(
            "SELECT * FROM target_remote_cache WHERE cache_key = ?",
            ("coordinate:crash",),
        ).fetchone()
    finally:
        conn.close()
    assert intent["status"] == "installed"
    assert active is None
    staged = get_cache_dir(project_root) / intent["staged_path"]
    final_file = get_cache_dir(project_root) / intent["final_path"]
    assert not staged.exists()
    assert final_file.read_text(encoding="utf-8") == "crash generation"

    recovered_repository = TargetCacheRepository(project_root, clock)
    recovered = recovered_repository.get("coordinate:crash")
    conn = get_connection(project_root)
    try:
        remaining_intents = conn.execute(
            "SELECT COUNT(*) AS count "
            "FROM target_coordinate_publication_intents"
        ).fetchone()["count"]
    finally:
        conn.close()
    assert recovered is not None
    assert recovered.generation_id == intent["generation_id"]
    assert recovered.payload == {"path": intent["final_path"]}
    assert final_file.read_text(encoding="utf-8") == "crash generation"
    assert remaining_intents == 0


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


def test_fallback_health_is_bounded_and_does_not_expose_paths(project_root, clock):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.service import TargetSearchService

    cache = TargetCacheRepository(project_root=project_root, clock=clock)
    fresh = cache.upsert(
        "target:fresh",
        "target",
        "UniProt",
        "P00533",
        {"gene_symbol": "EGFR"},
    )
    cache.upsert(
        "target:stale",
        "target",
        "UniProt",
        "O76074",
        {"gene_symbol": "PDE5A"},
        ttl=timedelta(seconds=1),
    )
    assert cache.mark_refresh_failure(
        fresh.cache_key,
        "timeout",
        expected_generation_id=fresh.generation_id,
    )
    clock.value += timedelta(seconds=2)

    service = TargetSearchService(
        project_root=project_root,
        resolver=_QueueResolver(),
        cache=cache,
        auto_seed=False,
    )
    health = service.get_fallback_health()

    assert set(health) == {
        "local_target_count",
        "local_structure_count",
        "seed_available",
        "cache_entry_count",
        "stale_entry_count",
        "last_refresh_errors",
    }
    assert health == {
        "local_target_count": 0,
        "local_structure_count": 0,
        "seed_available": False,
        "cache_entry_count": 2,
        "stale_entry_count": 1,
        "last_refresh_errors": {"timeout": 1},
    }
    assert "db_path" not in repr(health).lower()
    assert str(project_root) not in repr(health)


def test_cleanup_target_cache_cli_deletes_at_most_requested_limit(
    project_root, clock, monkeypatch, capsys
):
    from scripts.cleanup_target_cache import main
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_connection

    monkeypatch.setenv("TARGET_DB_PATH", str(project_root / "runtime" / "targets.sqlite"))
    monkeypatch.setenv("TARGET_CACHE_DIR", str(project_root / "runtime" / "cache"))
    repository = TargetCacheRepository(project_root=project_root, clock=clock)
    for index in range(12):
        repository.upsert(
            f"target:cli:{index}",
            "target",
            "UniProt",
            str(index),
            {"index": index},
            ttl=timedelta(seconds=1),
        )
    clock.value += timedelta(seconds=2)

    assert main(["--limit", "10"], project_root=project_root, clock=clock) == 0
    output = json.loads(capsys.readouterr().out)

    conn = get_connection(project_root)
    try:
        remaining = conn.execute(
            "SELECT COUNT(*) AS count FROM target_remote_cache"
        ).fetchone()["count"]
    finally:
        conn.close()
    assert output == {"files_deleted": 0, "records_deleted": 10}
    assert remaining == 2
    assert str(project_root) not in json.dumps(output)


def test_successful_structure_download_registers_managed_coordinate_for_90_days(
    project_root, clock, monkeypatch
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.database import get_cache_dir
    from src.target_search.downloader import StructureDownloader

    class Response:
        headers = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"data_test\n_atom_site.id\n1\n"

    cache = TargetCacheRepository(project_root=project_root, clock=clock)
    downloader = StructureDownloader(project_root=project_root, cache=cache)
    monkeypatch.setattr(
        "src.target_search.downloader.requests.get", lambda *a, **k: Response()
    )
    monkeypatch.setattr(downloader, "_mark_downloaded", lambda *a, **k: None)
    structure = {
        "id": 17,
        "structure_id": "1ABC",
        "source": "RCSB_PDB",
        "file_format": "cif",
        "local_file_path": "data/target_db/cache/rcsb/PDE5A/1ABC.cif",
        "download_url": "https://files.rcsb.org/download/1ABC.cif",
    }

    result = downloader.prepare_structure_file(structure)
    evidence = cache.get("coordinate:RCSB_PDB:1ABC:cif")

    assert result["success"] is True
    assert evidence is not None
    assert evidence.record_type == "coordinate_file"
    assert evidence.expires_at == clock.value + timedelta(days=90)
    assert Path(result["file_path"]).is_file()
    assert Path(result["file_path"]).is_relative_to(get_cache_dir(project_root))
    assert (
        Path(result["file_path"])
        .relative_to(get_cache_dir(project_root))
        .as_posix()
        == evidence.payload["path"]
    )


def test_failed_structure_download_does_not_register_coordinate(
    project_root, clock, monkeypatch
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.downloader import StructureDownloadError, StructureDownloader

    class Response:
        headers = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"not-a-coordinate"

    cache = TargetCacheRepository(project_root=project_root, clock=clock)
    downloader = StructureDownloader(project_root=project_root, cache=cache)
    monkeypatch.setattr(
        "src.target_search.downloader.requests.get", lambda *a, **k: Response()
    )
    structure = {
        "id": 18,
        "structure_id": "2BAD",
        "source": "RCSB_PDB",
        "file_format": "cif",
        "local_file_path": "data/target_db/cache/rcsb/BAD/2BAD.cif",
        "download_url": "https://files.rcsb.org/download/2BAD.cif",
    }

    with pytest.raises(StructureDownloadError, match="validation"):
        downloader.prepare_structure_file(structure)

    assert cache.get("coordinate:RCSB_PDB:2BAD:cif", allow_stale=True) is None


def test_compressed_structure_download_is_registered_and_remains_readable(
    project_root, clock, monkeypatch
):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.downloader import StructureDownloader

    compressed = gzip.compress(b"data_test\n_atom_site.id\n1\n")

    class Response:
        headers = {}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield compressed

    cache = TargetCacheRepository(project_root=project_root, clock=clock)
    downloader = StructureDownloader(project_root=project_root, cache=cache)
    monkeypatch.setattr(
        "src.target_search.downloader.requests.get", lambda *a, **k: Response()
    )
    monkeypatch.setattr(downloader, "_mark_downloaded", lambda *a, **k: None)
    structure = {
        "id": 19,
        "structure_id": "3GZP",
        "source": "RCSB_PDB",
        "file_format": "cif.gz",
        "local_file_path": "data/target_db/cache/rcsb/PDE5A/3GZP.cif.gz",
        "download_url": "https://files.rcsb.org/download/3GZP.cif.gz",
    }

    result = downloader.prepare_structure_file(structure)

    assert gzip.decompress(Path(result["file_path"]).read_bytes()).startswith(
        b"data_test"
    )
    assert cache.get("coordinate:RCSB_PDB:3GZP:cif.gz") is not None


class _FakeRemoteResponse:
    def __init__(
        self,
        status_code=200,
        payload=None,
        *,
        headers=None,
        raw=None,
        iteration_error=None,
    ):
        self.status_code = status_code
        self.headers = headers or {}
        self._raw = (
            json.dumps(payload).encode("utf-8") if raw is None else raw
        )
        self._iteration_error = iteration_error
        self.closed = False

    def iter_content(self, chunk_size=8192):
        if self._iteration_error is not None:
            raise self._iteration_error
        for start in range(0, len(self._raw), chunk_size):
            yield self._raw[start : start + chunk_size]

    def close(self):
        self.closed = True


class _FakeRemoteSession:
    def __init__(self, *scripted):
        self.scripted = list(scripted)
        self.calls = []
        self.close_calls = 0

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.scripted:
            raise AssertionError("unexpected remote request")
        result = self.scripted.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def close(self):
        self.close_calls += 1


class _ManualMonotonic:
    def __init__(self, value=0.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class _CapturingAdapter(requests.adapters.BaseAdapter):
    def __init__(self, payload=b"{}"):
        self.payload = payload
        self.prepared_requests = []
        self.send_kwargs = []

    def send(self, request, **kwargs):
        self.prepared_requests.append(request)
        self.send_kwargs.append(kwargs)
        response = requests.Response()
        response.status_code = 200
        response._content = self.payload
        response._content_consumed = True
        response.request = request
        response.url = request.url
        return response

    def close(self):
        pass


class _RedirectCapturingAdapter(requests.adapters.BaseAdapter):
    def __init__(self):
        self.prepared_requests = []

    def send(self, request, **kwargs):
        self.prepared_requests.append(request)
        response = requests.Response()
        response.request = request
        response.url = request.url
        if len(self.prepared_requests) == 1:
            response.status_code = 302
            response.headers["Location"] = (
                "https://alphafold.ebi.ac.uk/private?token=redirect-secret"
            )
            response.headers["Set-Cookie"] = "session=redirect-secret"
            response._content = b"provider redirect body redirect-secret"
        else:
            response.status_code = 200
            response._content = b"{}"
        response._content_consumed = True
        return response

    def close(self):
        pass


def _bounded_remote_client(session, sleeps=None, **overrides):
    from src.target_search.remote_clients import BoundedJsonClient

    options = {
        "session": session,
        "sleep": (sleeps if sleeps is not None else []).append,
        "timeout": (1.0, 2.0),
        "max_attempts": 3,
        "backoff_base": 0.25,
        "backoff_ceiling": 2.0,
        "max_response_bytes": 4096,
    }
    options.update(overrides)
    return BoundedJsonClient(**options)


_UNIPROT_TEST_URL = "https://rest.uniprot.org/uniprotkb/search"
_RCSB_SEARCH_TEST_URL = "https://search.rcsb.org/rcsbsearch/v2/query"
_RCSB_ENTRY_TEST_URL = "https://data.rcsb.org/rest/v1/core/entry/1ABC"
_ALPHAFOLD_TEST_URL = "https://alphafold.ebi.ac.uk/api/prediction/P00533"


def _rcsb_graphql_entry(
    entry_id,
    entity_id="1",
    *,
    accession="P00533",
    database_name="UniProt",
    organism="Homo sapiens",
    taxonomy_id=9606,
    method="X-RAY DIFFRACTION",
    resolution=1.8,
    title="Experimental structure",
    ligands=None,
):
    info = {}
    if resolution is not None:
        info["resolution_combined"] = (
            resolution if isinstance(resolution, list) else [resolution]
        )
    if ligands is not None:
        info["nonpolymer_bound_components"] = ligands
    return {
        "rcsb_id": entry_id,
        "struct": {"title": title} if title is not None else None,
        "exptl": [{"method": method}] if method is not None else [],
        "rcsb_entry_info": info,
        "polymer_entities": [
            {
                "rcsb_id": f"{entry_id}_{entity_id}",
                "rcsb_polymer_entity_container_identifiers": {
                    "entry_id": entry_id,
                    "entity_id": entity_id,
                    "reference_sequence_identifiers": [
                        {
                            "database_name": database_name,
                            "database_accession": accession,
                        }
                    ],
                },
                "rcsb_entity_source_organism": [
                    {
                        "ncbi_scientific_name": organism,
                        "ncbi_taxonomy_id": taxonomy_id,
                    }
                ],
            }
        ],
    }


def test_uniprot_resolve_constructs_exact_human_query_and_normalizes_record():
    from src.target_search.remote_clients import STABLE_USER_AGENT, UniProtClient

    response = _FakeRemoteResponse(
        payload={
            "results": [
                {
                    "primaryAccession": "P00533",
                    "uniProtkbId": "EGFR_HUMAN",
                    "proteinDescription": {
                        "recommendedName": {
                            "fullName": {"value": "Epidermal growth factor receptor"}
                        }
                    },
                    "genes": [
                        {
                            "geneName": {"value": "EGFR"},
                            "synonyms": [
                                {"value": "ERBB"},
                                {"value": "ERBB1"},
                                {"value": "EGFR"},
                            ],
                        }
                    ],
                    "organism": {"scientificName": "Homo sapiens", "taxonId": 9606},
                }
            ]
        }
    )
    session = _FakeRemoteSession(response)

    result = UniProtClient(_bounded_remote_client(session)).resolve("egfr")

    assert result == {
        "gene_symbol": "EGFR",
        "protein_name": "Epidermal growth factor receptor",
        "uniprot_id": "P00533",
        "organism": "Homo sapiens",
        "aliases": ["ERBB", "ERBB1"],
        "source": "UniProt",
        "source_record_id": "P00533",
        "source_url": "https://www.uniprot.org/uniprotkb/P00533/entry",
        "match_reason": "exact_gene",
    }
    assert session.calls == [
        (
            "GET",
            "https://rest.uniprot.org/uniprotkb/search",
            {
                "params": {
                    "query": "(gene_exact:EGFR) AND (organism_id:9606)",
                    "format": "json",
                    "size": 5,
                    "fields": "accession,id,gene_names,protein_name,organism_name,organism_id,reviewed",
                },
                "headers": {
                    "Accept": "application/json",
                    "User-Agent": STABLE_USER_AGENT,
                },
                "auth": session.calls[0][2]["auth"],
                "allow_redirects": False,
                "timeout": (1.0, 2.0),
                "stream": True,
            },
        )
    ]
    assert "Authorization" not in session.calls[0][2]["headers"]
    assert response.closed is True


def test_uniprot_exact_primary_gene_match_wins_over_alias_match():
    from src.target_search.remote_clients import UniProtClient

    session = _FakeRemoteSession(
        _FakeRemoteResponse(
            payload={
                "results": [
                    {
                        "primaryAccession": "Q00001",
                        "genes": [{"geneName": {"value": "OTHER"}, "synonyms": [{"value": "EGFR"}]}],
                        "organism": {"scientificName": "Homo sapiens", "taxonId": 9606},
                    },
                    {
                        "primaryAccession": "P00533",
                        "genes": [{"geneName": {"value": "EGFR"}}],
                        "organism": {"scientificName": "Homo sapiens", "taxonId": 9606},
                    },
                ]
            }
        )
    )

    result = UniProtClient(_bounded_remote_client(session)).resolve("EGFR")

    assert result["uniprot_id"] == "P00533"
    assert result["match_reason"] == "exact_gene"


def test_uniprot_accession_query_uses_exact_accession_field():
    from src.target_search.remote_clients import UniProtClient

    session = _FakeRemoteSession(
        _FakeRemoteResponse(
            payload={
                "results": [
                    {
                        "primaryAccession": "P00533",
                        "genes": [{"geneName": {"value": "EGFR"}}],
                        "organism": {"scientificName": "Homo sapiens", "taxonId": 9606},
                    }
                ]
            }
        )
    )

    result = UniProtClient(_bounded_remote_client(session)).resolve("p00533")

    assert result["match_reason"] == "exact_accession"
    assert session.calls[0][2]["params"]["query"] == (
        "(accession:P00533) AND (organism_id:9606)"
    )


def test_uniprot_multiple_equally_plausible_matches_are_explicitly_ambiguous():
    from src.target_search.remote_clients import RemoteSourceError, UniProtClient

    session = _FakeRemoteSession(
        _FakeRemoteResponse(
            payload={
                "results": [
                    {"primaryAccession": "P00533", "genes": [{"geneName": {"value": "EGFR"}}], "organism": {"scientificName": "Homo sapiens", "taxonId": 9606}},
                    {"primaryAccession": "Q9H0H5", "genes": [{"geneName": {"value": "EGFR"}}], "organism": {"scientificName": "Homo sapiens", "taxonId": 9606}},
                ]
            }
        )
    )

    with pytest.raises(RemoteSourceError) as raised:
        UniProtClient(_bounded_remote_client(session)).resolve("EGFR")

    assert raised.value.source == "UniProt"
    assert raised.value.code == "ambiguous_match"
    assert raised.value.retryable is False
    assert raised.value.status_code is None


def test_uniprot_prefers_reviewed_exact_gene_over_unreviewed_exact_matches():
    from src.target_search.remote_clients import UniProtClient

    session = _FakeRemoteSession(
        _FakeRemoteResponse(
            payload={
                "results": [
                    {
                        "primaryAccession": "G5E9C5",
                        "entryType": "UniProtKB unreviewed (TrEMBL)",
                        "genes": [{"geneName": {"value": "PDE5A"}}],
                        "organism": {
                            "scientificName": "Homo sapiens",
                            "taxonId": 9606,
                        },
                    },
                    {
                        "primaryAccession": "O76074",
                        "entryType": "UniProtKB reviewed (Swiss-Prot)",
                        "genes": [{"geneName": {"value": "PDE5A"}}],
                        "organism": {
                            "scientificName": "Homo sapiens",
                            "taxonId": 9606,
                        },
                    },
                ]
            }
        )
    )

    result = UniProtClient(_bounded_remote_client(session)).resolve("PDE5A")

    assert result["uniprot_id"] == "O76074"
    assert result["match_reason"] == "exact_gene_reviewed"


def test_uniprot_no_exact_result_returns_none():
    from src.target_search.remote_clients import UniProtClient

    session = _FakeRemoteSession(
        _FakeRemoteResponse(
            payload={
                "results": [
                    {"primaryAccession": "Q00001", "genes": [{"geneName": {"value": "OTHER"}}]}
                ]
            }
        )
    )

    assert UniProtClient(_bounded_remote_client(session)).resolve("EGFR") is None


def test_bounded_json_client_returns_none_for_404_without_retrying():
    session = _FakeRemoteSession(_FakeRemoteResponse(status_code=404, raw=b"secret body"))

    result = _bounded_remote_client(session).request_json(
        "UniProt", "GET", _UNIPROT_TEST_URL
    )

    assert result is None
    assert len(session.calls) == 1


def test_bounded_json_client_retries_timeout_with_capped_backoff():
    sleeps = []
    session = _FakeRemoteSession(
        requests.Timeout("query=secret"), _FakeRemoteResponse(payload={"ok": True})
    )

    result = _bounded_remote_client(session, sleeps, backoff_base=5.0).request_json(
        "RCSB_PDB", "GET", _RCSB_ENTRY_TEST_URL
    )

    assert result == {"ok": True}
    assert sleeps == [2.0]
    assert len(session.calls) == 2


def test_bounded_json_client_retries_timeout_while_streaming_response():
    sleeps = []
    first = _FakeRemoteResponse(iteration_error=requests.Timeout("body=secret"))
    session = _FakeRemoteSession(first, _FakeRemoteResponse(payload={"ok": True}))

    result = _bounded_remote_client(session, sleeps).request_json(
        "RCSB_PDB", "GET", _RCSB_ENTRY_TEST_URL
    )

    assert result == {"ok": True}
    assert sleeps == [0.25]
    assert first.closed is True


def test_bounded_json_client_retries_connection_failure_and_reports_exhaustion():
    from src.target_search.remote_clients import RemoteSourceError

    session = _FakeRemoteSession(
        requests.ConnectionError("token=first"),
        requests.ConnectionError("token=second"),
    )

    with pytest.raises(RemoteSourceError) as raised:
        _bounded_remote_client(session, max_attempts=2).request_json(
            "AlphaFold", "GET", _ALPHAFOLD_TEST_URL
        )

    assert raised.value.code == "connection_failure"
    assert raised.value.retryable is True
    assert raised.value.status_code is None
    assert len(session.calls) == 2


def test_bounded_json_client_honors_numeric_retry_after_with_ceiling():
    sleeps = []
    session = _FakeRemoteSession(
        _FakeRemoteResponse(status_code=429, headers={"Retry-After": "30"}, raw=b"slow down"),
        _FakeRemoteResponse(payload={"ok": True}),
    )

    result = _bounded_remote_client(session, sleeps).request_json(
        "UniProt", "GET", _UNIPROT_TEST_URL
    )

    assert result == {"ok": True}
    assert sleeps == [2.0]


def test_bounded_json_client_retries_provider_5xx_then_succeeds():
    sleeps = []
    session = _FakeRemoteSession(
        _FakeRemoteResponse(status_code=503, raw=b"provider internals"),
        _FakeRemoteResponse(payload={"ok": True}),
    )

    result = _bounded_remote_client(session, sleeps).request_json(
        "RCSB_PDB", "POST", _RCSB_SEARCH_TEST_URL, json={"safe": True}
    )

    assert result == {"ok": True}
    assert sleeps == [0.25]


@pytest.mark.parametrize(
    ("response", "expected_code", "expected_status"),
    [
        (_FakeRemoteResponse(raw=b"not-json"), "malformed_json", 200),
        (_FakeRemoteResponse(status_code=400, raw=b"provider says token=secret"), "http_client_error", 400),
    ],
)
def test_bounded_json_client_reports_nonretryable_sanitized_errors(
    response, expected_code, expected_status
):
    from src.target_search.remote_clients import RemoteSourceError

    with pytest.raises(RemoteSourceError) as raised:
        _bounded_remote_client(_FakeRemoteSession(response)).request_json(
            "UniProt", "GET", _UNIPROT_TEST_URL
        )

    error = raised.value
    assert (error.source, error.code, error.retryable, error.status_code) == (
        "UniProt",
        expected_code,
        False,
        expected_status,
    )
    loggable = f"{error!s} {error!r} {vars(error)}".lower()
    assert "secret" not in loggable
    assert "provider says" not in loggable
    assert "provider says" not in loggable


def test_bounded_json_client_rejects_oversized_content_length_without_reading_body():
    from src.target_search.remote_clients import RemoteSourceError

    response = _FakeRemoteResponse(
        headers={"Content-Length": "5000"}, raw=b"small fake body"
    )

    with pytest.raises(RemoteSourceError) as raised:
        _bounded_remote_client(
            _FakeRemoteSession(response), max_response_bytes=100
        ).request_json("UniProt", "GET", _UNIPROT_TEST_URL)

    assert raised.value.code == "response_too_large"
    assert raised.value.retryable is False
    assert response.closed is True


def test_bounded_json_client_rejects_oversized_stream_without_parsing_it():
    from src.target_search.remote_clients import RemoteSourceError

    with pytest.raises(RemoteSourceError) as raised:
        _bounded_remote_client(
            _FakeRemoteSession(_FakeRemoteResponse(raw=b"x" * 101)),
            max_response_bytes=100,
        ).request_json("UniProt", "GET", _UNIPROT_TEST_URL)

    assert raised.value.code == "response_too_large"


def test_remote_error_redacts_exception_and_provider_text_on_exhausted_5xx():
    from src.target_search.remote_clients import RemoteSourceError

    responses = [
        _FakeRemoteResponse(status_code=500, raw=b"authorization bearer secret")
        for _ in range(2)
    ]

    with pytest.raises(RemoteSourceError) as raised:
        _bounded_remote_client(
            _FakeRemoteSession(*responses), max_attempts=2
        ).request_json(
            "RCSB_PDB", "GET", _RCSB_ENTRY_TEST_URL
        )

    error = raised.value
    assert (error.code, error.retryable, error.status_code) == (
        "provider_unavailable",
        True,
        500,
    )
    assert "secret" not in f"{error!s} {error!r} {vars(error)}".lower()


@pytest.mark.parametrize(
    ("client_name", "identifier"),
    [("uniprot", "EGFR) OR (*:*"), ("rcsb", "P00533?token=x"), ("alphafold", "not an accession")],
)
def test_remote_clients_reject_unsafe_identifiers_before_request(client_name, identifier):
    from src.target_search.remote_clients import (
        AlphaFoldClient,
        RcsbClient,
        RemoteSourceError,
        UniProtClient,
    )

    session = _FakeRemoteSession()
    http = _bounded_remote_client(session)
    operation = {
        "uniprot": lambda: UniProtClient(http).resolve(identifier),
        "rcsb": lambda: RcsbClient(http).search(identifier),
        "alphafold": lambda: AlphaFoldClient(http).lookup(identifier),
    }[client_name]

    with pytest.raises(RemoteSourceError) as raised:
        operation()

    assert raised.value.code == "invalid_identifier"
    assert raised.value.retryable is False
    assert session.calls == []


def test_uniprot_rejects_invalid_provider_response_shape():
    from src.target_search.remote_clients import RemoteSourceError, UniProtClient

    with pytest.raises(RemoteSourceError) as raised:
        UniProtClient(
            _bounded_remote_client(_FakeRemoteSession(_FakeRemoteResponse(payload=[])))
        ).resolve("EGFR")

    assert raised.value.code == "invalid_response"
    assert raised.value.retryable is False


def test_rcsb_search_is_bounded_deterministic_and_normalizes_evidence():
    from src.target_search.remote_clients import RcsbClient

    search_response = _FakeRemoteResponse(
        payload={
            "result_set": [
                {"identifier": "2def_2"},
                {"identifier": "1ABC_1"},
                {"identifier": "2DEF_2"},
                {"identifier": "9ZZZ_1"},
            ]
        }
    )
    entries = _FakeRemoteResponse(
        payload={
            "data": {
                "entries": [
                    _rcsb_graphql_entry(
                        "2DEF",
                        "2",
                        method="ELECTRON MICROSCOPY",
                        resolution=3,
                        title="Second structure",
                    ),
                    _rcsb_graphql_entry(
                        "1ABC",
                        title="EGFR kinase with inhibitor",
                        ligands=["ATP", "LIG", "ATP"],
                    ),
                ]
            }
        }
    )
    session = _FakeRemoteSession(search_response, entries)

    results = RcsbClient(_bounded_remote_client(session), max_structures=2).search(
        "p00533"
    )

    assert results == [
        {
            "structure_id": "1ABC",
            "source": "RCSB_PDB",
            "structure_type": "experimental",
            "method": "X-RAY DIFFRACTION",
            "resolution": 1.8,
            "organism": "Homo sapiens",
            "title": "EGFR kinase with inhibitor",
            "ligand_evidence": ["ATP", "LIG"],
            "download_url": "https://files.rcsb.org/download/1ABC.cif",
            "source_url": "https://www.rcsb.org/structure/1ABC",
        },
        {
            "structure_id": "2DEF",
            "source": "RCSB_PDB",
            "structure_type": "experimental",
            "method": "ELECTRON MICROSCOPY",
            "resolution": 3.0,
            "organism": "Homo sapiens",
            "title": "Second structure",
            "ligand_evidence": [],
            "download_url": "https://files.rcsb.org/download/2DEF.cif",
            "source_url": "https://www.rcsb.org/structure/2DEF",
        },
    ]
    assert [call[1] for call in session.calls] == [
        "https://search.rcsb.org/rcsbsearch/v2/query",
        "https://data.rcsb.org/graphql",
    ]


def test_rcsb_missing_optional_fields_are_safe():
    from src.target_search.remote_clients import RcsbClient

    session = _FakeRemoteSession(
        _FakeRemoteResponse(payload={"result_set": [{"identifier": "3XYZ_1"}]}),
        _FakeRemoteResponse(
            payload={
                "data": {
                    "entries": [
                        _rcsb_graphql_entry(
                            "3XYZ", resolution=None, title=None, ligands=None
                        )
                    ]
                }
            }
        ),
    )

    results = RcsbClient(_bounded_remote_client(session), max_structures=2).search(
        "P00533"
    )

    assert results == [
        {
            "structure_id": "3XYZ",
            "source": "RCSB_PDB",
            "structure_type": "experimental",
            "method": "X-RAY DIFFRACTION",
            "resolution": None,
            "organism": "Homo sapiens",
            "title": None,
            "ligand_evidence": [],
            "download_url": "https://files.rcsb.org/download/3XYZ.cif",
            "source_url": "https://www.rcsb.org/structure/3XYZ",
        }
    ]


def test_rcsb_official_empty_search_204_returns_no_structures():
    from src.target_search.remote_clients import RcsbClient

    session = _FakeRemoteSession(_FakeRemoteResponse(status_code=204, raw=b""))

    assert RcsbClient(_bounded_remote_client(session)).search("P00533") == []
    assert len(session.calls) == 1


def test_alphafold_lookup_constructs_official_request_and_stays_predicted():
    from src.target_search.remote_clients import AlphaFoldClient

    session = _FakeRemoteSession(
        _FakeRemoteResponse(
            payload=[
                {
                    "entryId": "AF-P00533-F1",
                    "modelEntityId": "AF-P00533-F1",
                    "uniprotAccession": "P00533",
                    "latestVersion": 6,
                    "uniprotDescription": "Epidermal growth factor receptor",
                    "organismScientificName": "Homo sapiens",
                    "pdbUrl": "https://alphafold.ebi.ac.uk/files/AF-P00533-F1-model_v6.pdb",
                    "cifUrl": "https://alphafold.ebi.ac.uk/files/AF-P00533-F1-model_v6.cif",
                }
            ]
        )
    )

    result = AlphaFoldClient(_bounded_remote_client(session)).lookup("p00533")

    assert result == {
        "structure_id": "AF-P00533-F1",
        "model_id": "AF-P00533-F1",
        "uniprot_id": "P00533",
        "source": "AlphaFold",
        "structure_type": "predicted",
        "resolution": None,
        "title": "Epidermal growth factor receptor",
        "organism": "Homo sapiens",
        "pdb_url": "https://alphafold.ebi.ac.uk/files/AF-P00533-F1-model_v6.pdb",
        "cif_url": "https://alphafold.ebi.ac.uk/files/AF-P00533-F1-model_v6.cif",
        "download_url": "https://alphafold.ebi.ac.uk/files/AF-P00533-F1-model_v6.cif",
        "source_url": "https://alphafold.ebi.ac.uk/entry/P00533",
    }
    assert session.calls[0][0:2] == (
        "GET",
        "https://alphafold.ebi.ac.uk/api/prediction/P00533",
    )
    assert result["structure_type"] != "experimental"


def test_alphafold_empty_result_returns_none_and_invalid_shape_is_rejected():
    from src.target_search.remote_clients import AlphaFoldClient, RemoteSourceError

    assert (
        AlphaFoldClient(
            _bounded_remote_client(_FakeRemoteSession(_FakeRemoteResponse(payload=[])))
        ).lookup("P00533")
        is None
    )

    with pytest.raises(RemoteSourceError) as raised:
        AlphaFoldClient(
            _bounded_remote_client(
                _FakeRemoteSession(_FakeRemoteResponse(payload={"entryId": "AF-P00533-F1"}))
            )
        ).lookup("P00533")
    assert raised.value.code == "invalid_response"


def test_remote_source_error_has_exact_immutable_sanitized_fields():
    from src.target_search.remote_clients import RemoteSourceError

    error = RemoteSourceError(
        "UniProt", "http_client_error", retryable=False, status_code=400
    )

    assert (
        error.source,
        error.code,
        error.retryable,
        error.status_code,
    ) == ("UniProt", "http_client_error", False, 400)
    assert set(vars(error)) == {"source", "code", "retryable", "status_code"}
    assert not hasattr(error, "status")
    assert "provider body" not in str(error).lower()
    for name, value in (
        ("source", "AlphaFold"),
        ("code", "timeout"),
        ("retryable", True),
        ("status_code", 500),
        ("extra", "provider body"),
        ("args", ("provider body",)),
    ):
        with pytest.raises(AttributeError):
            setattr(error, name, value)
    with pytest.raises(AttributeError):
        del error.code
    with pytest.raises(TypeError):
        vars(error)["extra"] = "provider body"


def test_bounded_client_rejects_injected_session_auth_without_leaking_it():
    session = requests.Session()
    session.auth = ("private-user", "private-password")

    with pytest.raises(ValueError) as raised:
        _bounded_remote_client(session)

    assert "private" not in str(raised.value).lower()


@pytest.mark.parametrize("header_name", ["Authorization", "Proxy-Authorization"])
def test_bounded_client_rejects_default_authorization_headers(header_name):
    session = requests.Session()
    session.headers[header_name] = "Bearer private-token"

    with pytest.raises(ValueError) as raised:
        _bounded_remote_client(session)

    assert "private-token" not in str(raised.value)


@pytest.mark.parametrize("credential_source", ["header", "cookie_jar"])
def test_bounded_client_rejects_session_cookies(credential_source):
    session = requests.Session()
    if credential_source == "header":
        session.headers["Cookie"] = "session=private-cookie"
    else:
        session.cookies.set("session", "private-cookie")

    with pytest.raises(ValueError) as raised:
        _bounded_remote_client(session)

    assert "private-cookie" not in str(raised.value)


def test_bounded_client_rejects_credential_bearing_session_proxy():
    session = requests.Session()
    session.proxies["https"] = "https://private-user:private-password@proxy.example"

    with pytest.raises(ValueError) as raised:
        _bounded_remote_client(session)

    assert "private" not in str(raised.value).lower()


def test_bounded_client_final_prepared_request_has_no_auth_or_cookie(monkeypatch):
    monkeypatch.setattr(
        requests.sessions,
        "get_netrc_auth",
        lambda url: ("netrc-user", "netrc-password"),
    )
    session = requests.Session()
    adapter = _CapturingAdapter()
    session.mount("https://", adapter)

    result = _bounded_remote_client(session).request_json(
        "UniProt", "GET", "https://rest.uniprot.org/uniprotkb/search"
    )

    assert result == {}
    prepared = adapter.prepared_requests[0]
    assert {
        name.casefold() for name in prepared.headers
    }.isdisjoint({"authorization", "proxy-authorization", "cookie"})


def test_internally_created_session_disables_environment_credentials(monkeypatch):
    from src.target_search.remote_clients import BoundedJsonClient

    monkeypatch.setattr(
        requests.sessions,
        "get_netrc_auth",
        lambda url: ("netrc-user", "netrc-password"),
    )
    client = BoundedJsonClient(sleep=lambda delay: None)
    adapter = _CapturingAdapter()
    client._session.mount("https://", adapter)

    result = client.request_json(
        "AlphaFold", "GET", "https://alphafold.ebi.ac.uk/api/prediction/P00533"
    )

    assert result == {}
    assert client._session.trust_env is False
    assert "Authorization" not in adapter.prepared_requests[0].headers


def test_bounded_json_client_generic_request_exception_is_nonretryable_and_redacted():
    from src.target_search.remote_clients import RemoteSourceError

    session = _FakeRemoteSession(requests.RequestException("token=private"))

    with pytest.raises(RemoteSourceError) as raised:
        _bounded_remote_client(session).request_json(
            "UniProt", "GET", _UNIPROT_TEST_URL
        )

    assert raised.value.code == "request_failure"
    assert raised.value.retryable is False
    assert raised.value.status_code is None
    assert len(session.calls) == 1
    assert "private" not in f"{raised.value!s} {raised.value!r}"


def test_bounded_json_client_exhausted_timeout_is_retryable_and_bounded():
    from src.target_search.remote_clients import RemoteSourceError

    sleeps = []
    session = _FakeRemoteSession(
        requests.Timeout("first private body"),
        requests.Timeout("second private body"),
    )

    with pytest.raises(RemoteSourceError) as raised:
        _bounded_remote_client(session, sleeps, max_attempts=2).request_json(
            "AlphaFold", "GET", _ALPHAFOLD_TEST_URL
        )

    assert (
        raised.value.code,
        raised.value.retryable,
        raised.value.status_code,
    ) == ("timeout", True, None)
    assert sleeps == [0.25]
    assert len(session.calls) == 2


def test_bounded_json_client_exhausted_429_keeps_numeric_status():
    from src.target_search.remote_clients import RemoteSourceError

    sleeps = []
    session = _FakeRemoteSession(
        _FakeRemoteResponse(status_code=429, headers={"Retry-After": "0.75"}),
        _FakeRemoteResponse(status_code=429, headers={"Retry-After": "0.75"}),
    )

    with pytest.raises(RemoteSourceError) as raised:
        _bounded_remote_client(session, sleeps, max_attempts=2).request_json(
            "UniProt", "GET", _UNIPROT_TEST_URL
        )

    assert (
        raised.value.code,
        raised.value.retryable,
        raised.value.status_code,
    ) == ("provider_unavailable", True, 429)
    assert sleeps == [0.75]
    assert len(session.calls) == 2


@pytest.mark.parametrize(
    ("response", "expected_status"),
    [
        (_FakeRemoteResponse(status_code="200"), None),
        (_FakeRemoteResponse(raw="not-bytes"), 200),
    ],
)
def test_bounded_json_client_rejects_invalid_status_and_chunk_shapes(
    response, expected_status
):
    from src.target_search.remote_clients import RemoteSourceError

    with pytest.raises(RemoteSourceError) as raised:
        _bounded_remote_client(_FakeRemoteSession(response)).request_json(
            "RCSB_PDB", "GET", _RCSB_ENTRY_TEST_URL
        )

    assert raised.value.code == "invalid_response"
    assert raised.value.retryable is False
    assert raised.value.status_code == expected_status


@pytest.mark.parametrize(
    "entry",
    [
        {"struct": {"title": "Missing identity"}},
        {"rcsb_id": "2DEF"},
    ],
)
def test_rcsb_requires_matching_entry_identity(entry):
    from src.target_search.remote_clients import RcsbClient, RemoteSourceError

    session = _FakeRemoteSession(
        _FakeRemoteResponse(payload={"result_set": [{"identifier": "1ABC_1"}]}),
        _FakeRemoteResponse(payload={"data": {"entries": [entry]}}),
    )

    with pytest.raises(RemoteSourceError) as raised:
        RcsbClient(_bounded_remote_client(session), max_structures=1).search("P00533")

    assert raised.value.code == "invalid_response"
    assert raised.value.retryable is False


@pytest.mark.parametrize(
    "resolution_values",
    [[], [float("nan")], [float("inf")], [0], [-1], [True], [1.8, "2.0"]],
)
def test_rcsb_rejects_every_malformed_resolution_member(resolution_values):
    from src.target_search.remote_clients import RcsbClient, RemoteSourceError

    session = _FakeRemoteSession(
        _FakeRemoteResponse(payload={"result_set": [{"identifier": "1ABC_1"}]}),
        _FakeRemoteResponse(
            payload={"data": {"entries": [
                _rcsb_graphql_entry("1ABC", resolution=resolution_values)
            ]}}
        ),
    )

    with pytest.raises(RemoteSourceError) as raised:
        RcsbClient(_bounded_remote_client(session), max_structures=1).search("P00533")

    assert raised.value.code == "invalid_response"
    assert raised.value.retryable is False


def test_alphafold_accepts_one_model_bound_coordinate_url():
    from src.target_search.remote_clients import AlphaFoldClient

    session = _FakeRemoteSession(
        _FakeRemoteResponse(
            payload=[
                {
                    "entryId": "AF-P00533-F1",
                    "modelEntityId": "AF-P00533-F1",
                    "uniprotAccession": "P00533",
                    "latestVersion": 6,
                    "pdbUrl": "https://alphafold.ebi.ac.uk/files/AF-P00533-F1-model_v6.pdb",
                }
            ]
        )
    )

    result = AlphaFoldClient(_bounded_remote_client(session)).lookup("P00533")

    assert result["pdb_url"].endswith("AF-P00533-F1-model_v6.pdb")
    assert result["cif_url"] is None
    assert result["download_url"] == result["pdb_url"]


@pytest.mark.parametrize(
    "coordinate_fields",
    [
        {},
        {
            "pdbUrl": "https://alphafold.ebi.ac.uk/files/AF-Q9H0H5-F1-model_v6.pdb"
        },
        {
            "cifUrl": "https://alphafold.ebi.ac.uk/files/AF-P00533-F2-model_v6.cif"
        },
        {"pdbUrl": "https://example.org/files/AF-P00533-F1-model_v6.pdb"},
    ],
)
def test_alphafold_rejects_absent_or_mismatched_coordinate_urls(coordinate_fields):
    from src.target_search.remote_clients import AlphaFoldClient, RemoteSourceError

    record = {
        "entryId": "AF-P00533-F1",
        "modelEntityId": "AF-P00533-F1",
        "uniprotAccession": "P00533",
        "latestVersion": 6,
        **coordinate_fields,
    }
    session = _FakeRemoteSession(_FakeRemoteResponse(payload=[record]))

    with pytest.raises(RemoteSourceError) as raised:
        AlphaFoldClient(_bounded_remote_client(session)).lookup("P00533")

    assert raised.value.code == "invalid_response"
    assert raised.value.retryable is False


def test_alphafold_filters_isoforms_and_selects_latest_canonical_model():
    from src.target_search.remote_clients import AlphaFoldClient

    session = _FakeRemoteSession(
        _FakeRemoteResponse(
            payload=[
                {
                    "entryId": "AF-P00533-2-F1",
                    "modelEntityId": "AF-P00533-2-F1",
                    "uniprotAccession": "P00533-2",
                    "latestVersion": 99,
                    "pdbUrl": "https://alphafold.ebi.ac.uk/files/AF-P00533-2-F1-model_v99.pdb",
                },
                {
                    "entryId": "AF-P00533-F1",
                    "modelEntityId": "AF-P00533-F1",
                    "uniprotAccession": "P00533",
                    "latestVersion": 5,
                    "pdbUrl": "https://alphafold.ebi.ac.uk/files/AF-P00533-F1-model_v5.pdb",
                },
                {
                    "entryId": "AF-P00533-F1",
                    "modelEntityId": "AF-P00533-F1",
                    "uniprotAccession": "P00533",
                    "latestVersion": 6,
                    "cifUrl": "https://alphafold.ebi.ac.uk/files/AF-P00533-F1-model_v6.cif",
                },
            ]
        )
    )

    result = AlphaFoldClient(_bounded_remote_client(session)).lookup("P00533")

    assert result["model_id"] == "AF-P00533-F1"
    assert result["cif_url"].endswith("-model_v6.cif")
    assert result["pdb_url"] is None


@pytest.mark.parametrize(
    "record_update",
    [
        {"modelEntityId": "AF-Q9H0H5-F1"},
        {"entryId": "AF-P00533-F2", "modelEntityId": "AF-P00533-F2"},
        {"latestVersion": 0},
        {
            "latestVersion": 6,
            "pdbUrl": "https://alphafold.ebi.ac.uk/files/AF-P00533-F1-model_v5.pdb",
        },
    ],
)
def test_alphafold_canonical_identity_and_version_are_strictly_bound(record_update):
    from src.target_search.remote_clients import AlphaFoldClient, RemoteSourceError

    record = {
        "entryId": "AF-P00533-F1",
        "modelEntityId": "AF-P00533-F1",
        "uniprotAccession": "P00533",
        "latestVersion": 6,
        "pdbUrl": "https://alphafold.ebi.ac.uk/files/AF-P00533-F1-model_v6.pdb",
        **record_update,
    }
    session = _FakeRemoteSession(_FakeRemoteResponse(payload=[record]))

    with pytest.raises(RemoteSourceError) as raised:
        AlphaFoldClient(_bounded_remote_client(session)).lookup("P00533")

    assert raised.value.code == "invalid_response"
    assert raised.value.retryable is False


def test_bounded_client_blocks_credential_bearing_redirect_without_second_request():
    from src.target_search.remote_clients import RemoteSourceError

    session = requests.Session()
    adapter = _RedirectCapturingAdapter()
    session.mount("https://", adapter)

    with pytest.raises(RemoteSourceError) as raised:
        _bounded_remote_client(session).request_json(
            "UniProt", "GET", "https://rest.uniprot.org/uniprotkb/search"
        )

    error = raised.value
    assert (error.code, error.retryable, error.status_code) == (
        "unexpected_status",
        False,
        302,
    )
    assert len(adapter.prepared_requests) == 1
    assert {
        name.casefold() for name in adapter.prepared_requests[0].headers
    }.isdisjoint({"authorization", "proxy-authorization", "cookie"})
    loggable = f"{error!s} {error!r} {vars(error)}".lower()
    assert "redirect-secret" not in loggable
    assert "alphafold.ebi.ac.uk/private" not in loggable


@pytest.mark.parametrize(
    ("source", "method", "url"),
    [
        ("UniProt", "GET", "http://rest.uniprot.org/uniprotkb/search"),
        ("UniProt", "POST", _UNIPROT_TEST_URL),
        ("UniProt", "GET", "https://user:pass@rest.uniprot.org/uniprotkb/search"),
        ("UniProt", "GET", "https://rest.uniprot.org:443/uniprotkb/search"),
        ("UniProt", "GET", "https://rest.uniprot.org/uniprotkb/search#fragment"),
        ("UniProt", "GET", "https://rest.uniprot.org/uniprotkb/P00533"),
        ("UniProt", "GET", "https://169.254.169.254/latest/meta-data/iam"),
        ("RCSB_PDB", "POST", "https://data.rcsb.org/not-graphql"),
        ("AlphaFold", "GET", "https://alphafold.ebi.ac.uk/api/prediction/not-valid"),
    ],
)
def test_bounded_client_rejects_nonallowlisted_requests_before_session(
    source, method, url
):
    from src.target_search.remote_clients import RemoteSourceError

    session = _FakeRemoteSession()

    with pytest.raises(RemoteSourceError) as raised:
        _bounded_remote_client(session).request_json(source, method, url)

    assert raised.value.code == "invalid_request"
    assert raised.value.retryable is False
    assert raised.value.status_code is None
    assert session.calls == []
    assert "169.254" not in str(raised.value)
    assert "user:pass" not in str(raised.value)


def test_borrowed_session_disables_environment_proxy_credentials_at_adapter(monkeypatch):
    monkeypatch.setenv(
        "HTTPS_PROXY", "http://env-user:env-password@proxy.invalid:8080"
    )
    session = requests.Session()
    adapter = _CapturingAdapter()
    session.mount("https://", adapter)

    result = _bounded_remote_client(session).request_json(
        "UniProt", "GET", _UNIPROT_TEST_URL
    )

    assert result == {}
    assert session.trust_env is False
    effective_proxies = adapter.send_kwargs[0]["proxies"]
    assert not effective_proxies
    assert "env-user" not in repr(effective_proxies)


def test_borrowed_session_environment_mutation_is_neutralized_before_request(
    monkeypatch,
):
    monkeypatch.setenv(
        "HTTPS_PROXY", "http://late-user:late-password@proxy.invalid:8080"
    )
    session = requests.Session()
    adapter = _CapturingAdapter()
    session.mount("https://", adapter)
    client = _bounded_remote_client(session)
    session.trust_env = True

    result = client.request_json("UniProt", "GET", _UNIPROT_TEST_URL)

    assert result == {}
    assert session.trust_env is False
    assert adapter.send_kwargs[0]["proxies"] == {}
    assert "late-user" not in repr(adapter.send_kwargs[0]["proxies"])


@pytest.mark.parametrize("mutation", ["auth", "header", "proxy"])
def test_borrowed_session_credential_mutation_is_rejected_before_request(mutation):
    from src.target_search.remote_clients import RemoteSourceError

    session = requests.Session()
    adapter = _CapturingAdapter()
    session.mount("https://", adapter)
    client = _bounded_remote_client(session)
    if mutation == "auth":
        session.auth = ("late-user", "late-password")
    elif mutation == "header":
        session.headers["Cookie"] = "late-secret"
    else:
        session.proxies["https"] = "http://late-user:late-password@proxy.invalid"

    with pytest.raises(RemoteSourceError) as raised:
        client.request_json("UniProt", "GET", _UNIPROT_TEST_URL)

    assert raised.value.code == "invalid_request"
    assert raised.value.retryable is False
    assert adapter.prepared_requests == []
    assert "late" not in str(raised.value).lower()


def test_borrowed_session_environment_is_revalidated_before_each_retry(monkeypatch):
    monkeypatch.setenv(
        "HTTPS_PROXY", "http://retry-user:retry-password@proxy.invalid:8080"
    )

    class RetryCapturingAdapter(requests.adapters.BaseAdapter):
        def __init__(self):
            self.send_kwargs = []

        def send(self, request, **kwargs):
            self.send_kwargs.append(kwargs)
            if len(self.send_kwargs) == 1:
                raise requests.ConnectionError("first attempt")
            response = requests.Response()
            response.status_code = 200
            response._content = b"{}"
            response._content_consumed = True
            response.request = request
            response.url = request.url
            return response

        def close(self):
            pass

    session = requests.Session()
    adapter = RetryCapturingAdapter()
    session.mount("https://", adapter)

    def mutate_environment(_delay):
        session.trust_env = True

    client = _bounded_remote_client(
        session, sleep=mutate_environment, max_attempts=2
    )

    assert client.request_json("UniProt", "GET", _UNIPROT_TEST_URL) == {}
    assert len(adapter.send_kwargs) == 2
    assert adapter.send_kwargs[1]["proxies"] == {}
    assert session.trust_env is False


@pytest.mark.parametrize(
    ("status", "retry_after", "expected_delay"),
    [
        (429, "-4", 0.0),
        (503, "Thu, 01 Jan 2026 00:00:05 GMT", 2.0),
        (500, "Thu, 01 Jan 2026 00:00:00 GMT", 0.0),
    ],
)
def test_retry_after_supports_seconds_and_http_dates_with_clamping(
    status, retry_after, expected_delay
):
    from src.target_search.remote_clients import BoundedJsonClient

    sleeps = []
    session = _FakeRemoteSession(
        _FakeRemoteResponse(status_code=status, headers={"Retry-After": retry_after}),
        _FakeRemoteResponse(payload={"ok": True}),
    )
    client = BoundedJsonClient(
        session=session,
        sleep=sleeps.append,
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        timeout=(1.0, 2.0),
        max_attempts=2,
        backoff_base=0.25,
        backoff_ceiling=2.0,
    )

    result = client.request_json("UniProt", "GET", _UNIPROT_TEST_URL)

    assert result == {"ok": True}
    assert sleeps == [expected_delay]


def test_remote_source_error_pickle_roundtrip_preserves_only_sanitized_contract():
    from src.target_search.remote_clients import RemoteSourceError

    original = RemoteSourceError(
        "RCSB_PDB", "provider_unavailable", retryable=True, status_code=503
    )

    restored = pickle.loads(pickle.dumps(original))

    assert type(restored) is RemoteSourceError
    assert vars(restored) == vars(original)
    assert str(restored) == str(original)
    with pytest.raises(AttributeError):
        restored.extra = "provider-secret"


def test_bounded_client_context_closes_owned_but_not_borrowed_session(monkeypatch):
    from src.target_search.remote_clients import BoundedJsonClient

    owned = _FakeRemoteSession(_FakeRemoteResponse(payload={"ok": True}))
    monkeypatch.setattr(requests, "Session", lambda: owned)

    with BoundedJsonClient() as client:
        assert client.request_json("UniProt", "GET", _UNIPROT_TEST_URL) == {
            "ok": True
        }
    assert owned.close_calls == 1
    with pytest.raises(RuntimeError):
        client.request_json("UniProt", "GET", _UNIPROT_TEST_URL)

    borrowed = _FakeRemoteSession(_FakeRemoteResponse(payload={"ok": True}))
    with BoundedJsonClient(session=borrowed) as borrowed_client:
        assert borrowed_client.request_json(
            "UniProt", "GET", _UNIPROT_TEST_URL
        ) == {"ok": True}
    assert borrowed.close_calls == 0


@pytest.mark.parametrize("client_name", ["uniprot", "rcsb", "alphafold"])
def test_source_client_context_delegates_close(client_name):
    from src.target_search.remote_clients import AlphaFoldClient, RcsbClient, UniProtClient

    class ClosingTransport:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    transport = ClosingTransport()
    source_client = {
        "uniprot": UniProtClient,
        "rcsb": RcsbClient,
        "alphafold": AlphaFoldClient,
    }[client_name](transport)

    with source_client as entered:
        assert entered is source_client
    assert transport.close_calls == 1


def test_borrowed_session_request_calls_are_serialized_across_threads():
    from src.target_search.remote_clients import BoundedJsonClient

    class ConcurrentSession:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def request(self, method, url, **kwargs):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.03)
                return _FakeRemoteResponse(payload={"ok": True})
            finally:
                with self.lock:
                    self.active -= 1

    session = ConcurrentSession()
    client = BoundedJsonClient(session=session, sleep=lambda delay: None)
    barrier = threading.Barrier(5)
    results = []

    def worker():
        barrier.wait()
        results.append(client.request_json("UniProt", "GET", _UNIPROT_TEST_URL))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(3)

    assert all(not thread.is_alive() for thread in threads)
    assert results == [{"ok": True}] * 4
    assert session.max_active == 1


def test_fallback_unit_network_guard_fails_closed_without_explicit_adapter():
    with pytest.raises(AssertionError, match="explicit fake adapter"):
        requests.get(_UNIPROT_TEST_URL)


def test_uniprot_ranks_all_gene_names_and_collects_deterministic_aliases():
    from src.target_search.remote_clients import UniProtClient

    session = _FakeRemoteSession(
        _FakeRemoteResponse(
            payload={
                "results": [
                    {
                        "primaryAccession": "P00533",
                        "genes": [
                            {
                                "geneName": {"value": "ERBB_RECEPTOR"},
                                "synonyms": [{"value": "HER1"}],
                            },
                            {
                                "geneName": {"value": "EGFR"},
                                "synonyms": [
                                    {"value": "ERBB1"},
                                    {"value": "HER1"},
                                ],
                            },
                        ],
                        "organism": {
                            "scientificName": "Homo sapiens",
                            "taxonId": 9606,
                        },
                    }
                ]
            }
        )
    )

    result = UniProtClient(_bounded_remote_client(session)).resolve("EGFR")

    assert result["gene_symbol"] == "ERBB_RECEPTOR"
    assert result["aliases"] == ["EGFR", "ERBB1", "HER1"]
    assert result["match_reason"] == "exact_gene"


def test_uniprot_rejects_taxon_mismatch_in_plausible_human_record():
    from src.target_search.remote_clients import RemoteSourceError, UniProtClient

    session = _FakeRemoteSession(
        _FakeRemoteResponse(
            payload={
                "results": [
                    {
                        "primaryAccession": "P00533",
                        "genes": [{"geneName": {"value": "EGFR"}}],
                        "organism": {
                            "scientificName": "Mus musculus",
                            "taxonId": 10090,
                        },
                    }
                ]
            }
        )
    )

    with pytest.raises(RemoteSourceError) as raised:
        UniProtClient(_bounded_remote_client(session)).resolve("EGFR")

    assert raised.value.code == "invalid_response"
    assert raised.value.retryable is False


@pytest.mark.parametrize("organism", ["Homo sapiens", "human", "9606", 9606])
def test_uniprot_resolves_supported_human_aliases_to_taxon_query(organism):
    from src.target_search.remote_clients import UniProtClient

    session = _FakeRemoteSession(
        _FakeRemoteResponse(
            payload={
                "results": [
                    {
                        "primaryAccession": "P00533",
                        "genes": [{"geneName": {"value": "EGFR"}}],
                        "organism": {
                            "scientificName": "Homo sapiens",
                            "taxonId": 9606,
                        },
                    }
                ]
            }
        )
    )

    result = UniProtClient(_bounded_remote_client(session)).resolve(
        "EGFR", organism=organism
    )

    assert result["organism"] == "Homo sapiens"
    assert session.calls[0][2]["params"]["query"] == (
        "(gene_exact:EGFR) AND (organism_id:9606)"
    )


@pytest.mark.parametrize("organism", ["Mus musculus", "mouse", "10090", 10090])
def test_uniprot_resolves_supported_mouse_aliases_to_taxon_query(organism):
    from src.target_search.remote_clients import UniProtClient

    session = _FakeRemoteSession(
        _FakeRemoteResponse(
            payload={
                "results": [
                    {
                        "primaryAccession": "Q01279",
                        "genes": [{"geneName": {"value": "Egfr"}}],
                        "organism": {
                            "scientificName": "Mus musculus",
                            "taxonId": 10090,
                        },
                    }
                ]
            }
        )
    )

    result = UniProtClient(_bounded_remote_client(session)).resolve(
        "EGFR", organism=organism
    )

    assert result["organism"] == "Mus musculus"
    assert session.calls[0][2]["params"]["query"] == (
        "(gene_exact:EGFR) AND (organism_id:10090)"
    )


@pytest.mark.parametrize(
    "organism", ["H. sapiens", "Canis lupus", "7227", 7227, "", True]
)
def test_uniprot_rejects_unsupported_organisms_before_request(organism):
    from src.target_search.remote_clients import RemoteSourceError, UniProtClient

    session = _FakeRemoteSession()

    with pytest.raises(RemoteSourceError) as raised:
        UniProtClient(_bounded_remote_client(session)).resolve(
            "EGFR", organism=organism
        )

    assert raised.value.code == "invalid_identifier"
    assert raised.value.retryable is False
    assert session.calls == []


def test_uniprot_rejects_noncanonical_scientific_name_for_valid_taxon():
    from src.target_search.remote_clients import RemoteSourceError, UniProtClient

    session = _FakeRemoteSession(
        _FakeRemoteResponse(
            payload={
                "results": [
                    {
                        "primaryAccession": "P00533",
                        "genes": [{"geneName": {"value": "EGFR"}}],
                        "organism": {"scientificName": "human", "taxonId": 9606},
                    }
                ]
            }
        )
    )

    with pytest.raises(RemoteSourceError) as raised:
        UniProtClient(_bounded_remote_client(session)).resolve("EGFR", organism="human")

    assert raised.value.code == "invalid_response"
    assert raised.value.retryable is False


def test_uniprot_preserves_ambiguity_across_secondary_gene_names():
    from src.target_search.remote_clients import RemoteSourceError, UniProtClient

    records = []
    for accession in ("P00533", "Q9H0H5"):
        records.append(
            {
                "primaryAccession": accession,
                "genes": [
                    {"geneName": {"value": "PRIMARY"}},
                    {"geneName": {"value": "EGFR"}},
                ],
                "organism": {
                    "scientificName": "Homo sapiens",
                    "taxonId": 9606,
                },
            }
        )
    session = _FakeRemoteSession(_FakeRemoteResponse(payload={"results": records}))

    with pytest.raises(RemoteSourceError) as raised:
        UniProtClient(_bounded_remote_client(session)).resolve("EGFR")

    assert raised.value.code == "ambiguous_match"


def test_rcsb_uses_bounded_polymer_entity_search_and_one_graphql_batch():
    from src.target_search.remote_clients import RcsbClient

    session = _FakeRemoteSession(
        _FakeRemoteResponse(
            payload={
                "result_set": [
                    {"identifier": "2DEF_2"},
                    {"identifier": "1ABC_1"},
                    {"identifier": "2DEF_2"},
                    {"identifier": "9ZZZ_1"},
                ]
            }
        ),
        _FakeRemoteResponse(
            payload={
                "data": {
                    "entries": [
                        _rcsb_graphql_entry(
                            "2DEF",
                            "2",
                            method="ELECTRON MICROSCOPY",
                            resolution=3.0,
                            title="Second structure",
                        ),
                        _rcsb_graphql_entry(
                            "1ABC",
                            ligands=["LIG", "ATP", "ATP"],
                            title="EGFR kinase with inhibitor",
                        ),
                    ]
                }
            }
        ),
    )

    results = RcsbClient(_bounded_remote_client(session), max_structures=2).search(
        "P00533"
    )

    assert [item["structure_id"] for item in results] == ["1ABC", "2DEF"]
    assert results[0]["organism"] == "Homo sapiens"
    assert results[0]["ligand_evidence"] == ["ATP", "LIG"]
    assert results[1]["method"] == "ELECTRON MICROSCOPY"
    assert len(session.calls) == 2
    search_json = session.calls[0][2]["json"]
    assert search_json["return_type"] == "polymer_entity"
    assert search_json["query"]["type"] == "group"
    nodes = search_json["query"]["nodes"]
    assert [node["parameters"]["attribute"] for node in nodes] == [
        "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_name",
        "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_accession",
        "exptl.method",
    ]
    assert nodes[0]["parameters"]["value"] == "UniProt"
    assert nodes[1]["parameters"]["value"] == "P00533"
    assert nodes[2]["parameters"]["operator"] == "exists"
    assert search_json["request_options"] == {
        "paginate": {"start": 0, "rows": 2},
        "sort": [
            {"sort_by": "score", "direction": "desc"},
            {"sort_by": "rcsb_id", "direction": "asc"},
        ],
    }
    assert session.calls[1][0:2] == (
        "POST",
        "https://data.rcsb.org/graphql",
    )
    graphql_json = session.calls[1][2]["json"]
    assert graphql_json["variables"] == {"ids": ["1ABC", "2DEF"]}
    assert "polymer_entities" in graphql_json["query"]
    assert "rcsb_entity_source_organism" in graphql_json["query"]
    assert "reference_sequence_identifiers" in graphql_json["query"]


def test_rcsb_max_structures_counts_unique_entries_across_bounded_pages():
    from src.target_search.remote_clients import RcsbClient

    session = _FakeRemoteSession(
        _FakeRemoteResponse(
            payload={
                "total_count": 3,
                "result_set": [
                    {"identifier": "1ABC_1"},
                    {"identifier": "1ABC_2"},
                ],
            }
        ),
        _FakeRemoteResponse(
            payload={
                "total_count": 3,
                "result_set": [{"identifier": "2DEF_1"}],
            }
        ),
        _FakeRemoteResponse(
            payload={
                "data": {
                    "entries": [
                        _rcsb_graphql_entry("2DEF"),
                        _rcsb_graphql_entry("1ABC"),
                    ]
                }
            }
        ),
    )

    results = RcsbClient(_bounded_remote_client(session), max_structures=2).search(
        "P00533"
    )

    assert [result["structure_id"] for result in results] == ["1ABC", "2DEF"]
    assert len(session.calls) == 3
    assert [call[2]["json"]["request_options"]["paginate"] for call in session.calls[:2]] == [
        {"start": 0, "rows": 2},
        {"start": 2, "rows": 2},
    ]
    assert session.calls[2][2]["json"]["variables"] == {
        "ids": ["1ABC", "2DEF"]
    }


@pytest.mark.parametrize(
    "entry",
    [
        _rcsb_graphql_entry("1ABC", database_name="PDB"),
        _rcsb_graphql_entry("1ABC", accession="Q9H0H5"),
        _rcsb_graphql_entry("1ABC", organism=None),
        _rcsb_graphql_entry("1ABC", method=None),
    ],
)
def test_rcsb_graphql_identity_and_experiment_validation_fail_closed(entry):
    from src.target_search.remote_clients import RcsbClient, RemoteSourceError

    if entry["polymer_entities"][0]["rcsb_entity_source_organism"][0][
        "ncbi_scientific_name"
    ] is None:
        entry["polymer_entities"][0]["rcsb_entity_source_organism"] = []
    session = _FakeRemoteSession(
        _FakeRemoteResponse(payload={"result_set": [{"identifier": "1ABC_1"}]}),
        _FakeRemoteResponse(payload={"data": {"entries": [entry]}}),
    )

    with pytest.raises(RemoteSourceError) as raised:
        RcsbClient(_bounded_remote_client(session), max_structures=1).search("P00533")

    assert raised.value.code == "invalid_response"
    assert raised.value.retryable is False
    assert len(session.calls) == 2


def test_rcsb_organism_comes_only_from_matching_polymer_entity_source():
    from src.target_search.remote_clients import RcsbClient

    entry = _rcsb_graphql_entry("1ABC")
    entry["rcsb_entry_info"]["source_organism_names"] = ["Fabricated species"]
    session = _FakeRemoteSession(
        _FakeRemoteResponse(payload={"result_set": [{"identifier": "1ABC_1"}]}),
        _FakeRemoteResponse(payload={"data": {"entries": [entry]}}),
    )

    result = RcsbClient(_bounded_remote_client(session), max_structures=1).search(
        "P00533"
    )

    assert result[0]["organism"] == "Homo sapiens"


def test_rcsb_rejects_malformed_reference_members_even_with_a_valid_match():
    from src.target_search.remote_clients import RcsbClient, RemoteSourceError

    entry = _rcsb_graphql_entry("1ABC")
    references = entry["polymer_entities"][0][
        "rcsb_polymer_entity_container_identifiers"
    ]["reference_sequence_identifiers"]
    references.insert(0, None)
    session = _FakeRemoteSession(
        _FakeRemoteResponse(payload={"result_set": [{"identifier": "1ABC_1"}]}),
        _FakeRemoteResponse(payload={"data": {"entries": [entry]}}),
    )

    with pytest.raises(RemoteSourceError) as raised:
        RcsbClient(_bounded_remote_client(session), max_structures=1).search("P00533")

    assert raised.value.code == "invalid_response"
    assert raised.value.retryable is False


def test_operation_timeout_rejects_trickled_json_between_stream_chunks():
    from src.target_search.remote_clients import RemoteSourceError

    clock = _ManualMonotonic()

    class TrickleResponse(_FakeRemoteResponse):
        def iter_content(self, chunk_size=8192):
            yield b'{"ok":'
            clock.advance(0.6)
            yield b"true}"

    response = TrickleResponse(raw=b"")
    client = _bounded_remote_client(
        _FakeRemoteSession(response),
        monotonic=clock,
        operation_timeout=0.5,
    )

    with pytest.raises(RemoteSourceError) as raised:
        client.request_json("UniProt", "GET", _UNIPROT_TEST_URL)

    assert raised.value.code == "operation_timeout"
    assert raised.value.retryable is True
    assert raised.value.status_code is None
    assert response.closed is True


def test_operation_timeout_rejects_delayed_stream_eof_after_valid_json():
    from src.target_search.remote_clients import RemoteSourceError

    clock = _ManualMonotonic()

    class DelayedEofResponse(_FakeRemoteResponse):
        def iter_content(self, chunk_size=8192):
            yield b"{}"
            clock.advance(0.6)

    response = DelayedEofResponse(raw=b"")
    client = _bounded_remote_client(
        _FakeRemoteSession(response),
        monotonic=clock,
        operation_timeout=0.5,
    )

    with pytest.raises(RemoteSourceError) as raised:
        client.request_json("UniProt", "GET", _UNIPROT_TEST_URL)

    assert raised.value.code == "operation_timeout"
    assert raised.value.retryable is True
    assert response.closed is True


def test_operation_timeout_caps_attempt_timeout_and_retry_sleep():
    from src.target_search.remote_clients import RemoteSourceError

    clock = _ManualMonotonic()
    sleeps = []

    def sleep(delay):
        sleeps.append(delay)
        clock.advance(delay)

    session = _FakeRemoteSession(
        requests.Timeout("provider detail must stay private"),
        _FakeRemoteResponse(payload={"unexpected": "second attempt"}),
    )
    client = _bounded_remote_client(
        session,
        sleep=sleep,
        monotonic=clock,
        operation_timeout=0.4,
        backoff_base=2.0,
        backoff_ceiling=2.0,
    )

    with pytest.raises(RemoteSourceError) as raised:
        client.request_json("UniProt", "GET", _UNIPROT_TEST_URL)

    assert raised.value.code == "operation_timeout"
    assert raised.value.retryable is True
    assert sleeps == [0.4]
    assert len(session.calls) == 1
    assert session.calls[0][2]["timeout"] == (0.4, 0.4)
    assert "private" not in str(raised.value).lower()


def test_rcsb_operation_timeout_is_shared_between_search_and_graphql():
    from src.target_search.remote_clients import RcsbClient, RemoteSourceError

    clock = _ManualMonotonic()

    class DeadlineAfterCloseResponse(_FakeRemoteResponse):
        def close(self):
            super().close()
            clock.advance(0.6)

    session = _FakeRemoteSession(
        DeadlineAfterCloseResponse(
            payload={"result_set": [{"identifier": "1ABC_1"}]}
        ),
        _FakeRemoteResponse(payload={"data": {"entries": []}}),
    )
    client = _bounded_remote_client(
        session,
        monotonic=clock,
        operation_timeout=0.5,
    )

    with pytest.raises(RemoteSourceError) as raised:
        RcsbClient(client, max_structures=1).search("P00533")

    assert raised.value.code == "operation_timeout"
    assert raised.value.retryable is True
    assert len(session.calls) == 1


class _ResolverClient:
    def __init__(self, *, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []
        self.closed = 0

    def resolve(self, query, organism):
        self.calls.append((query, organism))
        if self.error is not None:
            raise self.error
        return self.result

    def search(self, accession):
        self.calls.append(accession)
        if self.error is not None:
            raise self.error
        return self.result

    def lookup(self, accession):
        self.calls.append(accession)
        if self.error is not None:
            raise self.error
        return self.result

    def close(self):
        self.closed += 1


def _authoritative_target():
    return {
        "gene_symbol": "EGFR",
        "protein_name": "Epidermal growth factor receptor",
        "uniprot_id": "P00533",
        "organism": "Homo sapiens",
        "aliases": ["ERBB1"],
        "source": "UniProt",
        "source_record_id": "P00533",
        "source_url": "https://www.uniprot.org/uniprotkb/P00533/entry",
        "match_reason": "exact_gene",
    }


def _experimental_structure(structure_id="1ABC"):
    return {
        "structure_id": structure_id,
        "source": "RCSB_PDB",
        "structure_type": "experimental",
        "method": "X-RAY DIFFRACTION",
        "resolution": 1.8,
        "organism": "Homo sapiens",
        "title": "EGFR complex",
        "ligand_evidence": ["ATP"],
        "download_url": f"https://files.rcsb.org/download/{structure_id}.cif",
        "source_url": f"https://www.rcsb.org/structure/{structure_id}",
    }


def _predicted_structure():
    return {
        "structure_id": "AF-P00533-F1",
        "model_id": "AF-P00533-F1",
        "uniprot_id": "P00533",
        "source": "AlphaFold",
        "structure_type": "predicted",
        "resolution": None,
        "title": "Epidermal growth factor receptor",
        "organism": "Homo sapiens",
        "pdb_url": "https://alphafold.ebi.ac.uk/files/AF-P00533-F1-model_v6.pdb",
        "cif_url": "https://alphafold.ebi.ac.uk/files/AF-P00533-F1-model_v6.cif",
        "download_url": "https://alphafold.ebi.ac.uk/files/AF-P00533-F1-model_v6.cif",
        "source_url": "https://alphafold.ebi.ac.uk/entry/P00533",
    }


def test_target_resolution_is_deeply_immutable_and_json_safe():
    from src.target_search.authoritative_resolver import TargetResolution

    resolution = TargetResolution(
        status="resolved",
        target=_authoritative_target(),
        structures=(_experimental_structure(),),
        warnings=("safe warning",),
        lookup_path=("UniProt", "RCSB_PDB"),
    )

    with pytest.raises(FrozenInstanceError):
        resolution.status = "not_found"
    with pytest.raises(TypeError):
        resolution.target["gene_symbol"] = "OTHER"
    with pytest.raises(TypeError):
        resolution.structures[0]["source"] = "OTHER"
    assert json.loads(json.dumps(resolution.to_dict())) == resolution.to_dict()
    assert resolution.to_dict()["structures"][0]["structure_id"] == "1ABC"
    with pytest.raises(TypeError):
        TargetResolution(status="resolved", target={"not_json": object()})


def test_authoritative_resolver_uses_rcsb_before_alphafold_and_falls_back():
    from src.target_search.authoritative_resolver import AuthoritativeTargetResolver

    uniprot = _ResolverClient(result=_authoritative_target())
    rcsb = _ResolverClient(result=[_experimental_structure()])
    alphafold = _ResolverClient(result=_predicted_structure())
    resolver = AuthoritativeTargetResolver(uniprot, rcsb, alphafold)

    experimental = resolver.resolve("EGFR", "Homo sapiens")

    assert experimental.status == "resolved"
    assert experimental.lookup_path == ("UniProt", "RCSB_PDB")
    assert [item["structure_id"] for item in experimental.structures] == ["1ABC"]
    assert alphafold.calls == []

    rcsb.result = []
    predicted = resolver.resolve("P00533", "Homo sapiens")

    assert predicted.lookup_path == ("UniProt", "RCSB_PDB", "AlphaFold")
    assert predicted.structures[0]["structure_type"] == "predicted"


def test_authoritative_resolver_reports_not_found_ambiguity_and_sanitized_failure():
    from src.target_search.authoritative_resolver import AuthoritativeTargetResolver
    from src.target_search.remote_clients import RemoteSourceError

    not_found = AuthoritativeTargetResolver(
        _ResolverClient(result=None), _ResolverClient(result=[]), _ResolverClient(result=None)
    ).resolve("UNKNOWN", "Homo sapiens")
    assert not_found.status == "not_found"
    assert not_found.target is None
    assert not_found.lookup_path == ("UniProt",)

    ambiguous = AuthoritativeTargetResolver(
        _ResolverClient(
            error=RemoteSourceError(
                "UniProt", "ambiguous_match", retryable=False
            )
        ),
        _ResolverClient(result=[]),
        _ResolverClient(result=None),
    ).resolve("EGFR", "Homo sapiens")
    assert ambiguous.status == "ambiguous"
    assert ambiguous.warnings == ("UniProt:ambiguous_match",)
    assert ambiguous.retryable is False
    assert ambiguous.source == "UniProt"
    assert ambiguous.code == "ambiguous_match"

    unavailable = AuthoritativeTargetResolver(
        _ResolverClient(
            error=RemoteSourceError(
                "UniProt", "provider_unavailable", retryable=True, status_code=503
            )
        ),
        _ResolverClient(result=[]),
        _ResolverClient(result=None),
    ).resolve("secret-provider-body", "Homo sapiens")
    assert unavailable.status == "unavailable"
    assert unavailable.warnings == ("UniProt:provider_unavailable",)
    assert unavailable.retryable is True
    assert unavailable.source == "UniProt"
    assert unavailable.code == "provider_unavailable"
    assert unavailable.to_dict()["retryable"] is True
    assert "secret-provider-body" not in str(unavailable.to_dict())


def test_authoritative_resolver_closes_only_clients_it_owns(monkeypatch):
    import src.target_search.authoritative_resolver as resolver_module

    borrowed = [_ResolverClient(result=None) for _ in range(3)]
    borrowed_resolver = resolver_module.AuthoritativeTargetResolver(*borrowed)
    borrowed_resolver.close()
    assert [client.closed for client in borrowed] == [0, 0, 0]

    created = []

    class FakeHttp:
        def close(self):
            pass

    def factory(_http):
        client = _ResolverClient(result=None)
        created.append(client)
        return client

    monkeypatch.setattr(resolver_module, "BoundedJsonClient", FakeHttp)
    monkeypatch.setattr(resolver_module, "UniProtClient", factory)
    monkeypatch.setattr(resolver_module, "RcsbClient", factory)
    monkeypatch.setattr(resolver_module, "AlphaFoldClient", factory)

    with resolver_module.AuthoritativeTargetResolver() as owned_resolver:
        assert owned_resolver is not None

    assert [client.closed for client in created] == [1, 1, 1]


def test_service_lazy_resolver_is_created_once_under_concurrency_and_closed_once(
    project_root, monkeypatch
):
    from src.target_search import service as service_module

    created = []

    class SlowResolver:
        def __init__(self):
            time.sleep(0.03)
            self.closed = 0
            created.append(self)

        def close(self):
            self.closed += 1

    monkeypatch.setattr(service_module, "AuthoritativeTargetResolver", SlowResolver)
    service = service_module.TargetSearchService(
        project_root=project_root,
        auto_seed=False,
    )
    barrier = threading.Barrier(8)
    resolved = []

    def get_resolver():
        barrier.wait()
        resolved.append(service._get_resolver())

    threads = [threading.Thread(target=get_resolver) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    service.close()
    service.close()

    assert len(created) == 1
    assert len({id(item) for item in resolved}) == 1
    assert created[0].closed == 1
    with pytest.raises(RuntimeError, match="closed"):
        service._get_resolver()


def test_service_close_waits_for_inflight_lazy_resolver_creation(project_root, monkeypatch):
    from src.target_search import service as service_module

    construction_started = threading.Event()
    release_construction = threading.Event()
    created = []

    class PausedResolver:
        def __init__(self):
            self.closed = 0
            created.append(self)
            construction_started.set()
            assert release_construction.wait(timeout=5)

        def close(self):
            self.closed += 1

    monkeypatch.setattr(service_module, "AuthoritativeTargetResolver", PausedResolver)
    service = service_module.TargetSearchService(
        project_root=project_root,
        auto_seed=False,
    )
    getter = threading.Thread(target=service._get_resolver)
    closer = threading.Thread(target=service.close)

    getter.start()
    assert construction_started.wait(timeout=2)
    closer.start()
    assert closer.is_alive()
    release_construction.set()
    getter.join(timeout=5)
    closer.join(timeout=5)

    assert not getter.is_alive()
    assert not closer.is_alive()
    assert len(created) == 1
    assert created[0].closed == 1
    with pytest.raises(RuntimeError, match="closed"):
        service._get_resolver()


class _QueueResolver:
    def __init__(self, *resolutions):
        self.resolutions = list(resolutions)
        self.calls = []
        self.closed = 0

    def resolve(self, query, organism):
        self.calls.append((query, organism))
        if len(self.resolutions) > 1:
            return self.resolutions.pop(0)
        return self.resolutions[0]

    def close(self):
        self.closed += 1


def _resolution(
    status="resolved",
    *,
    target=None,
    structures=None,
    warnings=(),
    lookup_path=None,
    retryable=False,
    source=None,
    code=None,
):
    from src.target_search.authoritative_resolver import TargetResolution

    return TargetResolution(
        status=status,
        target=_authoritative_target() if target is None and status == "resolved" else target,
        structures=tuple(
            [_experimental_structure()] if structures is None and status == "resolved" else structures or []
        ),
        warnings=tuple(warnings),
        lookup_path=tuple(lookup_path or ("UniProt", "RCSB_PDB")),
        retryable=retryable,
        source=source,
        code=code,
    )


def _remote_service(project_root, clock, resolver, *, auto_seed=False):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.service import TargetSearchService

    cache = TargetCacheRepository(project_root=project_root, clock=clock)
    return TargetSearchService(
        project_root=project_root,
        resolver=resolver,
        cache=cache,
        auto_seed=auto_seed,
    )


def _tamper_cache_row(project_root, cache_key, *, row_changes=None, payload_changes=None):
    from src.target_search.database import get_connection

    conn = get_connection(project_root)
    try:
        row = conn.execute(
            "SELECT payload_json FROM target_remote_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        assert row is not None
        assignments = []
        values = []
        if payload_changes:
            payload = json.loads(row["payload_json"])
            payload_changes(payload)
            payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
            assignments.extend(["payload_json = ?", "payload_digest = ?"])
            values.extend(
                [
                    payload_json,
                    hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                ]
            )
        for column, value in (row_changes or {}).items():
            assignments.append(f"{column} = ?")
            values.append(value)
        values.append(cache_key)
        conn.execute(
            f"UPDATE target_remote_cache SET {', '.join(assignments)} "
            "WHERE cache_key = ?",
            values,
        )
        conn.commit()
    finally:
        conn.close()


def _cache_rows(project_root):
    from src.target_search.database import get_connection

    conn = get_connection(project_root)
    try:
        return conn.execute(
            "SELECT cache_key, record_type, source, source_record_id, payload_json "
            "FROM target_remote_cache ORDER BY record_type"
        ).fetchall()
    finally:
        conn.close()


def test_target_search_local_hit_returns_without_authoritative_call(project_root, clock):
    from src.target_search.seed import seed_database

    seed_database(project_root)
    resolver = _QueueResolver(_resolution())
    service = _remote_service(project_root, clock, resolver)

    payload = service.search_targets("EGFR")

    assert payload["lookup_path"] == ["local"]
    assert payload["status"] == "resolved"
    assert payload["cache"] == {"target": "none", "structures": "none"}
    assert payload["results"][0]["gene_symbol"] == "EGFR"
    assert resolver.calls == []


def test_empty_local_database_seeds_existing_csv_once_before_remote(
    project_root, clock, monkeypatch
):
    from src.target_search import service as service_module
    from src.target_search.seed import seed_database as real_seed_database

    db_dir = project_root / "data" / "target_db"
    db_dir.mkdir(parents=True)
    (db_dir / "common_targets.csv").write_text(
        "gene_symbol,protein_name,uniprot_id,organism,target_type,aliases\n"
        "EGFR,Epidermal growth factor receptor,P00533,Homo sapiens,Kinase,ERBB1\n",
        encoding="utf-8",
    )
    seed_calls = []

    def counted_seed(root):
        seed_calls.append(root)
        return real_seed_database(root)

    monkeypatch.setattr(service_module, "seed_database", counted_seed)
    resolver = _QueueResolver(_resolution())
    service = _remote_service(project_root, clock, resolver, auto_seed=True)

    first = service.search_targets("EGFR")
    second = service.search_targets("ERBB1")

    assert first["lookup_path"] == ["local_seed", "local"]
    assert second["lookup_path"] == ["local"]
    assert len(seed_calls) == 1
    assert resolver.calls == []


def test_empty_database_without_seed_csv_goes_remote_without_creating_seed_files(
    project_root, clock
):
    resolver = _QueueResolver(_resolution())
    service = _remote_service(project_root, clock, resolver, auto_seed=True)

    payload = service.search_targets("EGFR")

    assert payload["lookup_path"][0] == "local"
    assert "UniProt" in payload["lookup_path"]
    assert resolver.calls == [("EGFR", "Homo sapiens")]
    assert not (project_root / "data" / "target_db" / "seed_targets.csv").exists()


def test_populated_local_miss_uses_authoritative_resolver_without_reseeding(
    project_root, clock, monkeypatch
):
    from src.target_search import service as service_module
    from src.target_search.database import get_connection, init_db

    init_db(project_root)
    conn = get_connection(project_root)
    try:
        conn.execute(
            "INSERT INTO targets (gene_symbol, uniprot_id) VALUES (?, ?)",
            ("OTHER", "Q00001"),
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(
        service_module,
        "seed_database",
        lambda _root: pytest.fail("populated databases must never be reseeded"),
    )
    resolver = _QueueResolver(_resolution())
    service = _remote_service(project_root, clock, resolver, auto_seed=True)

    payload = service.search_targets("EGFR")

    assert payload["results"][0]["source"] == "UniProt"
    assert resolver.calls == [("EGFR", "Homo sapiens")]


def test_remote_resolution_caches_target_and_structures_with_separate_ttls(
    project_root, clock
):
    from src.target_search.database import get_connection

    resolver = _QueueResolver(_resolution())
    service = _remote_service(project_root, clock, resolver)

    first = service.search_targets("EGFR")
    second = service.search_targets("EGFR")

    assert len(resolver.calls) == 1
    assert first["cache"] == {"target": "refreshed", "structures": "refreshed"}
    assert second["cache"] == {"target": "fresh", "structures": "fresh"}
    record = second["results"][0]
    assert record["target_retrieved_at"] == clock.value.isoformat()
    assert record["target_expires_at"] == (
        clock.value + timedelta(days=30)
    ).isoformat()
    assert record["structures_retrieved_at"] == clock.value.isoformat()
    assert record["structures_expires_at"] == (
        clock.value + timedelta(days=7)
    ).isoformat()
    assert record["recommended_structures"][0]["retrieved_at"] == clock.value.isoformat()
    assert record["recommended_structures"][0]["expires_at"] == (
        clock.value + timedelta(days=7)
    ).isoformat()
    conn = get_connection(project_root)
    try:
        rows = conn.execute(
            "SELECT record_type, retrieved_at, expires_at FROM target_remote_cache "
            "ORDER BY record_type"
        ).fetchall()
    finally:
        conn.close()
    assert [row["record_type"] for row in rows] == ["structures", "target"]
    ttl_by_type = {
        row["record_type"]: datetime.fromisoformat(row["expires_at"])
        - datetime.fromisoformat(row["retrieved_at"])
        for row in rows
    }
    assert ttl_by_type == {
        "target": timedelta(days=30),
        "structures": timedelta(days=7),
    }


def test_multiple_structures_normalize_idempotently_and_reuse_fresh_cache(
    project_root, clock
):
    lower_ranked = {
        **_experimental_structure("1LOW"),
        "resolution": 3.4,
        "ligand_evidence": [],
    }
    higher_ranked = _experimental_structure("2HIH")
    resolver = _QueueResolver(
        _resolution(structures=[lower_ranked, higher_ranked])
    )
    service = _remote_service(project_root, clock, resolver)

    first = service.search_targets("EGFR")
    second = service.search_targets("EGFR")

    assert resolver.calls == [("EGFR", "Homo sapiens")]
    assert first["cache"] == {"target": "refreshed", "structures": "refreshed"}
    assert second["cache"] == {"target": "fresh", "structures": "fresh"}
    for payload in (first, second):
        structures = payload["results"][0]["recommended_structures"]
        assert [item["structure_id"] for item in structures] == ["2HIH", "1LOW"]
        assert [item["is_preferred"] for item in structures] == [True, False]
    assert (
        first["results"][0]["recommended_structures"]
        == second["results"][0]["recommended_structures"]
    )


@pytest.mark.parametrize(
    ("target_changes", "structures"),
    [
        ({"source": "CommunityWiki"}, [_experimental_structure()]),
        ({"source_record_id": "Q9H0H5"}, [_experimental_structure()]),
        (
            {"source_url": "https://evil.example/uniprot/P00533"},
            [_experimental_structure()],
        ),
        ({"organism": "Mus musculus"}, [_experimental_structure()]),
        (
            {},
            [
                {
                    **_experimental_structure(),
                    "source_url": "https://evil.example/structure/1ABC",
                }
            ],
        ),
        (
            {},
            [{**_experimental_structure(), "source": "CommunityPDB"}],
        ),
        (
            {},
            [
                {
                    **_predicted_structure(),
                    "structure_id": "AF-Q9H0H5-F1",
                    "model_id": "AF-Q9H0H5-F1",
                }
            ],
        ),
    ],
    ids=[
        "untrusted-target-source",
        "record-id-mismatch",
        "evil-target-url",
        "organism-mismatch",
        "evil-structure-url",
        "untrusted-structure-source",
        "alphafold-accession-mismatch",
    ],
)
def test_untrusted_authoritative_resolution_is_rejected_before_any_cache_write(
    project_root, clock, target_changes, structures
):
    from src.target_search.database import get_connection

    target = {**_authoritative_target(), **target_changes}
    service = _remote_service(
        project_root,
        clock,
        _QueueResolver(_resolution(target=target, structures=structures)),
    )

    payload = service.search_targets("EGFR")

    assert payload["status"] == "unavailable"
    assert payload["results"] == []
    assert payload["warnings"] == ["authoritative_normalization_failed"]
    conn = get_connection(project_root)
    try:
        cache_rows = conn.execute(
            "SELECT COUNT(*) AS count FROM target_remote_cache"
        ).fetchone()["count"]
    finally:
        conn.close()
    assert cache_rows == 0


@pytest.mark.parametrize(
    ("row_changes", "payload_changes"),
    [
        ({"source": "CommunityWiki"}, None),
        ({"source_record_id": "Q9H0H5"}, None),
        (None, lambda payload: payload.update(organism="Mus musculus")),
        (None, lambda payload: payload.update(uniprot_id="Q9H0H5")),
        (None, lambda payload: payload.update(source_record_id="Q9H0H5")),
        (
            None,
            lambda payload: payload.update(
                source_url="https://evil.example/uniprot/P00533"
            ),
        ),
    ],
    ids=[
        "unofficial-row-source",
        "row-record-id-mismatch",
        "payload-organism-mismatch",
        "payload-accession-mismatch",
        "payload-record-id-mismatch",
        "payload-evil-url",
    ],
)
def test_invalid_cached_target_is_fully_removed_before_authoritative_fallback(
    project_root, clock, row_changes, payload_changes
):
    class InspectingResolver(_QueueResolver):
        def resolve(self, query, organism):
            if self.calls:
                assert _cache_rows(project_root) == []
            return super().resolve(query, organism)

    resolver = InspectingResolver(
        _resolution(),
        _resolution(structures=[_experimental_structure("2DEF")]),
    )
    service = _remote_service(project_root, clock, resolver)
    service.search_targets("EGFR")
    target_key = service._target_cache_key("EGFR")
    _tamper_cache_row(
        project_root,
        target_key,
        row_changes=row_changes,
        payload_changes=payload_changes,
    )

    payload = service.search_targets("EGFR")

    assert resolver.calls == [
        ("EGFR", "Homo sapiens"),
        ("EGFR", "Homo sapiens"),
    ]
    assert payload["status"] == "resolved"
    assert payload["results"][0]["source"] == "UniProt"
    assert payload["results"][0]["recommended_structures"][0]["structure_id"] == "2DEF"
    assert payload["cache"] == {"target": "refreshed", "structures": "refreshed"}


@pytest.mark.parametrize(
    ("row_changes", "payload_changes"),
    [
        ({"source": "CommunityPDB"}, None),
        ({"source_record_id": "Q9H0H5"}, None),
        (
            None,
            lambda payload: payload["structures"][0].update(
                organism="Mus musculus"
            ),
        ),
        (
            None,
            lambda payload: payload["structures"][0].update(
                source_url="https://evil.example/structure/1ABC"
            ),
        ),
        (
            None,
            lambda payload: payload["structures"][0].update(
                structure_id="9ZZZ"
            ),
        ),
    ],
    ids=[
        "unofficial-row-source",
        "row-accession-mismatch",
        "payload-organism-mismatch",
        "payload-evil-url",
        "payload-record-id-url-mismatch",
    ],
)
def test_invalid_cached_structures_are_removed_before_authoritative_fallback(
    project_root, clock, row_changes, payload_changes
):
    class InspectingResolver(_QueueResolver):
        def resolve(self, query, organism):
            if self.calls:
                rows = _cache_rows(project_root)
                assert len(rows) == 1
                assert rows[0]["record_type"] == "target"
            return super().resolve(query, organism)

    resolver = InspectingResolver(
        _resolution(),
        _resolution(structures=[_experimental_structure("2DEF")]),
    )
    service = _remote_service(project_root, clock, resolver)
    service.search_targets("EGFR")
    structures_key = service._structures_cache_key("P00533")
    _tamper_cache_row(
        project_root,
        structures_key,
        row_changes=row_changes,
        payload_changes=payload_changes,
    )

    payload = service.search_targets("EGFR")

    assert resolver.calls == [
        ("EGFR", "Homo sapiens"),
        ("P00533", "Homo sapiens"),
    ]
    assert payload["status"] == "resolved"
    assert payload["results"][0]["recommended_structures"][0]["structure_id"] == "2DEF"
    assert payload["cache"] == {"target": "fresh", "structures": "refreshed"}


def test_cache_miss_cleanup_does_not_delete_generation_published_after_read(
    project_root, clock, monkeypatch
):
    from src.target_search.cache import TargetCacheRepository

    cache = TargetCacheRepository(project_root=project_root, clock=clock)
    service = _remote_service(project_root, clock, _QueueResolver(_resolution()))
    service.cache = cache
    target_key = service._target_cache_key("EGFR")
    real_get = cache.get
    published = {}

    def get_then_publish(cache_key, allow_stale=False):
        evidence = real_get(cache_key, allow_stale=allow_stale)
        if cache_key == target_key and "evidence" not in published:
            assert evidence is None
            published["evidence"] = cache.upsert(
                target_key,
                "target",
                "UniProt",
                "P00533",
                _authoritative_target(),
            )
        return evidence

    monkeypatch.setattr(cache, "get", get_then_publish)

    assert service._read_target_cache(target_key) is None
    surviving = real_get(target_key, allow_stale=True)

    assert surviving is not None
    assert surviving.generation_id == published["evidence"].generation_id


def test_invalid_target_companion_cleanup_preserves_new_structure_generation(
    project_root, clock, monkeypatch
):
    from src.target_search.cache import TargetCacheRepository

    cache = TargetCacheRepository(project_root=project_root, clock=clock)
    service = _remote_service(project_root, clock, _QueueResolver(_resolution()))
    service.cache = cache
    service.search_targets("EGFR")
    target_key = service._target_cache_key("EGFR")
    structures_key = service._structures_cache_key("P00533")
    _tamper_cache_row(
        project_root,
        target_key,
        payload_changes=lambda payload: payload.update(
            source_url="https://evil.example/uniprot/P00533"
        ),
    )
    original_structures = cache.get(structures_key, allow_stale=True)
    assert original_structures is not None
    real_discard = cache.discard
    published = {}

    def publish_before_discard(cache_key, *, expected_generation_id):
        if cache_key == structures_key and "evidence" not in published:
            published["evidence"] = cache.upsert(
                structures_key,
                "structures",
                original_structures.source,
                original_structures.source_record_id,
                original_structures.to_dict()["payload"],
            )
        return real_discard(
            cache_key,
            expected_generation_id=expected_generation_id,
        )

    monkeypatch.setattr(cache, "discard", publish_before_discard)

    assert service._read_target_cache(target_key) is None
    surviving = cache.get(structures_key, allow_stale=True)

    assert surviving is not None
    assert surviving.generation_id == published["evidence"].generation_id


def test_target_cache_identity_includes_default_organism(project_root, clock):
    from src.target_search.cache import TargetCacheRepository
    from src.target_search.service import TargetSearchService

    mouse_target = {
        **_authoritative_target(),
        "uniprot_id": "Q01279",
        "organism": "Mus musculus",
        "source_record_id": "Q01279",
        "source_url": "https://www.uniprot.org/uniprotkb/Q01279/entry",
    }
    resolver = _QueueResolver(
        _resolution(),
        _resolution(target=mouse_target, structures=[]),
    )
    cache = TargetCacheRepository(project_root=project_root, clock=clock)
    human = TargetSearchService(
        project_root=project_root,
        resolver=resolver,
        cache=cache,
        auto_seed=False,
        organism="Homo sapiens",
    )
    mouse = TargetSearchService(
        project_root=project_root,
        resolver=resolver,
        cache=cache,
        auto_seed=False,
        organism="Mus musculus",
    )

    human_result = human.search_targets("EGFR")
    mouse_result = mouse.search_targets("EGFR")

    assert human_result["results"][0]["organism"] == "Homo sapiens"
    assert mouse_result["results"][0]["organism"] == "Mus musculus"
    assert resolver.calls == [
        ("EGFR", "Homo sapiens"),
        ("EGFR", "Mus musculus"),
    ]


def test_expired_structure_cache_refreshes_by_accession_and_retains_fresh_identity(
    project_root, clock
):
    changed_target = {**_authoritative_target(), "gene_symbol": "CHANGED"}
    resolver = _QueueResolver(
        _resolution(),
        _resolution(
            target=changed_target,
            structures=[_experimental_structure("2DEF")],
        ),
    )
    service = _remote_service(project_root, clock, resolver)
    service.search_targets("EGFR")
    clock.value += timedelta(days=8)

    refreshed = service.search_targets("EGFR")

    assert resolver.calls == [
        ("EGFR", "Homo sapiens"),
        ("P00533", "Homo sapiens"),
    ]
    assert refreshed["results"][0]["gene_symbol"] == "EGFR"
    assert refreshed["results"][0]["recommended_structures"][0]["structure_id"] == "2DEF"
    assert refreshed["cache"] == {"target": "fresh", "structures": "refreshed"}


def test_retryable_outage_can_use_expired_cache_with_explicit_stale_warning(
    project_root, clock
):
    resolver = _QueueResolver(
        _resolution(),
        _resolution(
            status="unavailable",
            warnings=("UniProt:provider_unavailable",),
            lookup_path=("UniProt",),
            retryable=True,
            source="UniProt",
            code="provider_unavailable",
        ),
    )
    service = _remote_service(project_root, clock, resolver)
    service.search_targets("EGFR")
    clock.value += timedelta(days=31)

    stale = service.search_targets("EGFR")

    assert stale["status"] == "resolved"
    assert stale["results"][0]["stale"] is True
    assert stale["cache"] == {"target": "stale", "structures": "stale"}
    assert "stale_authoritative_cache" in stale["warnings"]
    assert "UniProt:provider_unavailable" in stale["warnings"]


def test_nonretryable_provider_unavailable_cannot_use_expired_cache(
    project_root, clock
):
    resolver = _QueueResolver(
        _resolution(),
        _resolution(
            status="unavailable",
            warnings=("UniProt:provider_unavailable",),
            lookup_path=("UniProt",),
            retryable=False,
            source="UniProt",
            code="provider_unavailable",
        ),
    )
    service = _remote_service(project_root, clock, resolver)
    service.search_targets("EGFR")
    clock.value += timedelta(days=31)

    payload = service.search_targets("EGFR")

    assert payload["status"] == "unavailable"
    assert payload["results"] == []
    assert payload["cache"] == {"target": "expired", "structures": "expired"}


def test_fresh_target_without_structure_cache_does_not_claim_empty_fresh_evidence(
    project_root, clock
):
    from src.target_search.database import get_connection

    resolver = _QueueResolver(
        _resolution(),
        _resolution(
            status="unavailable",
            warnings=("RCSB_PDB:provider_unavailable",),
            lookup_path=("UniProt", "RCSB_PDB"),
            retryable=True,
            source="RCSB_PDB",
            code="provider_unavailable",
        ),
    )
    service = _remote_service(project_root, clock, resolver)
    service.search_targets("EGFR")
    conn = get_connection(project_root)
    try:
        conn.execute("DELETE FROM target_remote_cache WHERE record_type = 'structures'")
        conn.commit()
    finally:
        conn.close()

    payload = service.search_targets("EGFR")

    assert payload["status"] == "partial"
    assert payload["cache"] == {"target": "fresh", "structures": "unavailable"}
    assert payload["results"][0]["structure_evidence_status"] == "unavailable"
    assert payload["results"][0]["structure_count"] is None
    assert payload["results"][0]["recommended_structures"] == []
    assert "stale_authoritative_cache" not in payload["warnings"]
    conn = get_connection(project_root)
    try:
        structure_count = conn.execute(
            "SELECT COUNT(*) AS count FROM target_remote_cache "
            "WHERE record_type = 'structures'"
        ).fetchone()["count"]
    finally:
        conn.close()
    assert structure_count == 0


def test_fresh_target_uses_existing_stale_structures_on_retryable_outage(
    project_root, clock
):
    resolver = _QueueResolver(
        _resolution(),
        _resolution(
            status="unavailable",
            warnings=("RCSB_PDB:provider_unavailable",),
            lookup_path=("UniProt", "RCSB_PDB"),
            retryable=True,
            source="RCSB_PDB",
            code="provider_unavailable",
        ),
    )
    service = _remote_service(project_root, clock, resolver)
    service.search_targets("EGFR")
    clock.value += timedelta(days=8)

    payload = service.search_targets("EGFR")

    assert payload["status"] == "resolved"
    assert payload["cache"] == {"target": "fresh", "structures": "stale"}
    assert payload["results"][0]["stale"] is True
    assert payload["results"][0]["target_stale"] is False
    assert payload["results"][0]["structures_stale"] is True
    assert payload["results"][0]["structure_evidence_status"] == "stale"
    assert payload["results"][0]["recommended_structures"][0]["structure_id"] == "1ABC"
    assert payload["results"][0]["target_expires_at"] == (
        clock.value - timedelta(days=8) + timedelta(days=30)
    ).isoformat()
    assert payload["results"][0]["structures_expires_at"] == (
        clock.value - timedelta(days=1)
    ).isoformat()
    assert payload["results"][0]["recommended_structures"][0]["stale"] is True
    assert "stale_authoritative_cache" in payload["warnings"]
    from src.agent.tools.target_database_tool import TargetDatabaseTool

    evidence = TargetDatabaseTool._target_evidence(payload["results"][0])
    assert evidence[0]["source"] == "UniProt"
    assert evidence[0]["stale"] is False
    assert evidence[1]["source"] == "RCSB_PDB"
    assert evidence[1]["stale"] is True


@pytest.mark.parametrize(
    ("status", "warnings"),
    [
        ("not_found", ()),
        ("ambiguous", ("UniProt:ambiguous_match",)),
        ("unavailable", ("UniProt:invalid_identifier",)),
    ],
)
def test_expired_cache_is_not_success_for_nonretryable_resolution(
    project_root, clock, status, warnings
):
    resolver = _QueueResolver(
        _resolution(),
        _resolution(status=status, warnings=warnings, lookup_path=("UniProt",)),
    )
    service = _remote_service(project_root, clock, resolver)
    service.search_targets("EGFR")
    clock.value += timedelta(days=31)

    payload = service.search_targets("EGFR")

    assert payload["status"] == status
    assert payload["results"] == []
    assert payload["cache"] == {"target": "expired", "structures": "expired"}


def test_remote_normalization_filters_and_provenance_are_consistent_and_json_safe(
    project_root, clock
):
    target = {**_authoritative_target(), "target_type": "Kinase"}
    service = _remote_service(
        project_root,
        clock,
        _QueueResolver(_resolution(target=target)),
    )

    payload = service.search_targets(
        "EGFR",
        target_type="Kinase",
        source="RCSB_PDB",
        has_experimental=True,
        docking_recommended=True,
        has_ligand=True,
    )
    record = payload["results"][0]

    assert record["target_id"] is None
    assert record["source"] == "UniProt"
    assert record["source_record_id"] == "P00533"
    assert record["source_url"].startswith("https://www.uniprot.org/")
    assert record["retrieved_at"] == clock.value.isoformat()
    assert record["expires_at"] == (clock.value + timedelta(days=30)).isoformat()
    assert record["stale"] is False
    assert record["structure_count"] == 1
    assert record["experimental_structure_count"] == 1
    assert record["alphafold_structure_count"] == 0
    assert record["has_experimental_structure"] is True
    assert record["has_alphafold_structure"] is False
    assert record["recommended_structures"][0]["ligand_ids"] == ["ATP"]
    assert record["recommended_structures"][0]["docking_recommended"] is True
    assert str(project_root) not in str(payload)
    json.dumps(payload)

    filtered = service.search_targets("EGFR", source="AlphaFold")
    assert filtered["status"] == "resolved"
    assert filtered["results"] == []


def test_concurrent_empty_database_calls_seed_once_idempotently(
    project_root, clock, monkeypatch
):
    from src.target_search import service as service_module
    from src.target_search.seed import seed_database as real_seed_database

    db_dir = project_root / "data" / "target_db"
    db_dir.mkdir(parents=True)
    (db_dir / "common_targets.csv").write_text(
        "gene_symbol,protein_name,uniprot_id,organism,target_type,aliases\n"
        "EGFR,Epidermal growth factor receptor,P00533,Homo sapiens,Kinase,ERBB1\n",
        encoding="utf-8",
    )
    seed_calls = []

    def slow_seed(root):
        seed_calls.append(root)
        time.sleep(0.05)
        return real_seed_database(root)

    monkeypatch.setattr(service_module, "seed_database", slow_seed)
    resolver = _QueueResolver(_resolution())
    service = _remote_service(project_root, clock, resolver, auto_seed=True)
    barrier = threading.Barrier(5)
    payloads = []

    def search():
        barrier.wait()
        payloads.append(service.search_targets("EGFR"))

    threads = [threading.Thread(target=search) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(seed_calls) == 1
    assert len(payloads) == 5
    assert all(payload["results"][0]["gene_symbol"] == "EGFR" for payload in payloads)
    assert resolver.calls == []


def test_multiprocess_empty_database_calls_seed_once_under_db_path_lock(
    project_root,
):
    db_dir = project_root / "data" / "target_db"
    db_dir.mkdir(parents=True)
    (db_dir / "common_targets.csv").write_text(
        "gene_symbol,protein_name,uniprot_id,organism,target_type,aliases\n"
        "EGFR,Epidermal growth factor receptor,P00533,Homo sapiens,Kinase,ERBB1\n",
        encoding="utf-8",
    )
    call_log = project_root / "seed-calls.log"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    first_count_barrier = context.Barrier(4)
    output = context.Queue()
    processes = [
        context.Process(
            target=_auto_seed_process_worker,
            args=(
                str(project_root),
                start,
                first_count_barrier,
                str(call_log),
                output,
            ),
        )
        for _ in range(4)
    ]
    for process in processes:
        process.start()
    start.set()
    results = [output.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)

    assert all(not process.is_alive() for process in processes)
    assert all(process.exitcode == 0 for process in processes)
    assert all(status == "ok" for status, _ in results), results
    assert all(path[-1] == "local" for _, path in results)
    assert len(call_log.read_text(encoding="utf-8").splitlines()) == 1
