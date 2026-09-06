from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .redaction import contains_credential, redact_sensitive


def _json_dump(value: Any) -> str:
    return json.dumps(redact_sensitive(value), ensure_ascii=False, sort_keys=True)


def _json_load(value: str | None, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    return json.loads(value)


class SQLiteAgentStateStore:
    """SQLite-backed durable state store for Agent execution and curated memory."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS agent_runs (
            trace_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            skill_name TEXT,
            query TEXT,
            workflow_version TEXT,
            idempotency_key TEXT UNIQUE,
            user_id TEXT,
            session_id TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_checkpoints (
            id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            step_id TEXT NOT NULL,
            workflow_version TEXT,
            status TEXT NOT NULL,
            input_hash TEXT,
            tool_name TEXT,
            tool_version TEXT,
            adapter_version TEXT,
            model_version TEXT,
            output_json TEXT,
            error_json TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_checkpoints_trace_step
            ON agent_checkpoints(trace_id, step_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS agent_events (
            id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            event TEXT NOT NULL,
            skill TEXT,
            tool TEXT,
            message TEXT,
            progress REAL,
            payload_json TEXT,
            created_at REAL NOT NULL,
            UNIQUE(trace_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS tool_executions (
            id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            step_id TEXT,
            tool_name TEXT NOT NULL,
            status TEXT NOT NULL,
            input_json TEXT,
            output_json TEXT,
            error_json TEXT,
            elapsed_ms INTEGER,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workflow_artifacts (
            id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            step_id TEXT,
            path TEXT NOT NULL,
            sha256 TEXT,
            media_type TEXT,
            validated INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS long_term_memories (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            session_id TEXT,
            category TEXT NOT NULL,
            content_json TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 1.0,
            validated INTEGER NOT NULL,
            provenance_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS routing_feedback (
            id TEXT PRIMARY KEY,
            trace_id TEXT,
            query TEXT NOT NULL,
            selected_skill TEXT,
            corrected_skill TEXT,
            accepted INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS evaluation_runs (
            id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            metrics_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            completed_at REAL
        );
        CREATE TABLE IF NOT EXISTS evaluation_cases (
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL,
            version TEXT NOT NULL,
            dataset TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(case_id, version, dataset)
        );
        CREATE TABLE IF NOT EXISTS evaluation_results (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            status TEXT NOT NULL,
            score REAL,
            result_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
        """
        with self._lock, self._connect() as connection:
            connection.executescript(schema)

    def start_run(self, run: dict[str, Any]) -> None:
        data = redact_sensitive(run)
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_runs (
                    trace_id, status, skill_name, query, workflow_version,
                    idempotency_key, user_id, session_id, metadata_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trace_id) DO UPDATE SET
                    status=excluded.status,
                    skill_name=excluded.skill_name,
                    query=excluded.query,
                    workflow_version=excluded.workflow_version,
                    idempotency_key=excluded.idempotency_key,
                    user_id=excluded.user_id,
                    session_id=excluded.session_id,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    data["trace_id"],
                    data.get("status", "pending"),
                    data.get("skill_name"),
                    data.get("query"),
                    data.get("workflow_version"),
                    data.get("idempotency_key"),
                    data.get("user_id"),
                    data.get("session_id"),
                    _json_dump(data.get("metadata", {})),
                    now,
                    now,
                ),
            )

    def update_run_status(self, trace_id: str, status: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE agent_runs SET status = ?, updated_at = ? WHERE trace_id = ?",
                (status, time.time(), trace_id),
            )

    def update_run_metadata(
        self,
        trace_id: str,
        metadata: dict[str, Any],
    ) -> None:
        incoming = redact_sensitive(metadata)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM agent_runs WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
            if row is None:
                raise KeyError(trace_id)
            current = _json_load(row["metadata_json"], {})
            if not isinstance(current, dict):
                current = {}
            merged = redact_sensitive({**current, **incoming})
            connection.execute(
                """
                UPDATE agent_runs
                SET metadata_json = ?, updated_at = ?
                WHERE trace_id = ?
                """,
                (_json_dump(merged), time.time(), trace_id),
            )

    def get_run(self, trace_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_runs WHERE trace_id = ?", (trace_id,)
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["metadata"] = _json_load(result.pop("metadata_json"), {})
        return result

    def get_run_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT trace_id FROM agent_runs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return self.get_run(row["trace_id"]) if row else None

    def save_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        data = redact_sensitive(checkpoint)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO agent_checkpoints (
                    id, trace_id, step_id, workflow_version, status, input_hash,
                    tool_name, tool_version, adapter_version, model_version,
                    output_json, error_json, retry_count, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data.get("id", str(uuid4())),
                    data["trace_id"],
                    data["step_id"],
                    data.get("workflow_version"),
                    data.get("status", "pending"),
                    data.get("input_hash"),
                    data.get("tool_name"),
                    data.get("tool_version"),
                    data.get("adapter_version"),
                    data.get("model_version"),
                    _json_dump(data.get("output")) if "output" in data else None,
                    _json_dump(data.get("error")) if data.get("error") else None,
                    int(data.get("retry_count", 0)),
                    _json_dump(data.get("metadata", {})),
                    time.time(),
                ),
            )

    def latest_checkpoint(
        self, trace_id: str, step_id: str | None = None
    ) -> dict[str, Any] | None:
        query = "SELECT * FROM agent_checkpoints WHERE trace_id = ?"
        params: list[Any] = [trace_id]
        if step_id is not None:
            query += " AND step_id = ?"
            params.append(step_id)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        if not row:
            return None
        result = dict(row)
        result["output"] = _json_load(result.pop("output_json"), None)
        result["error"] = _json_load(result.pop("error_json"), None)
        result["metadata"] = _json_load(result.pop("metadata_json"), {})
        return result

    def append_event(self, event: dict[str, Any]) -> int:
        data = redact_sensitive(event)
        with self._lock, self._connect() as connection:
            event_id = data.get("id")
            if event_id:
                existing = connection.execute(
                    "SELECT sequence FROM agent_events WHERE id = ?",
                    (event_id,),
                ).fetchone()
                if existing:
                    return int(existing[0])
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_events WHERE trace_id = ?",
                (data["trace_id"],),
            ).fetchone()
            sequence = int(row[0])
            connection.execute(
                """
                INSERT INTO agent_events (
                    id, trace_id, sequence, event, skill, tool, message,
                    progress, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id or str(uuid4()),
                    data["trace_id"],
                    sequence,
                    data["event"],
                    data.get("skill"),
                    data.get("tool"),
                    data.get("message"),
                    data.get("progress"),
                    _json_dump(data.get("payload")) if "payload" in data else None,
                    data.get("timestamp", time.time()),
                ),
            )
        return sequence

    def get_events(self, trace_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_events WHERE trace_id = ? ORDER BY sequence",
                (trace_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = _json_load(item.pop("payload_json"), None)
            result.append(item)
        return result

    def record_tool_execution(self, execution: dict[str, Any]) -> str:
        data = redact_sensitive(execution)
        execution_id = data.get("id", str(uuid4()))
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO tool_executions (
                    id, trace_id, step_id, tool_name, status, input_json,
                    output_json, error_json, elapsed_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    data["trace_id"],
                    data.get("step_id"),
                    data["tool_name"],
                    data.get("status", "unknown"),
                    _json_dump(data.get("input")) if "input" in data else None,
                    _json_dump(data.get("output")) if "output" in data else None,
                    _json_dump(data.get("error")) if data.get("error") else None,
                    data.get("elapsed_ms"),
                    time.time(),
                ),
            )
        return execution_id

    def get_tool_executions(self, trace_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tool_executions WHERE trace_id = ? ORDER BY created_at",
                (trace_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["input"] = _json_load(item.pop("input_json"), None)
            item["output"] = _json_load(item.pop("output_json"), None)
            item["error"] = _json_load(item.pop("error_json"), None)
            result.append(item)
        return result

    def register_artifact(self, artifact: dict[str, Any]) -> str:
        data = redact_sensitive(artifact)
        artifact_id = data.get("id", str(uuid4()))
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workflow_artifacts (
                    id, trace_id, step_id, path, sha256, media_type,
                    validated, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    data["trace_id"],
                    data.get("step_id"),
                    data["path"],
                    data.get("sha256"),
                    data.get("media_type"),
                    int(bool(data.get("validated", False))),
                    _json_dump(data.get("metadata", {})),
                    time.time(),
                ),
            )
        return artifact_id

    def add_memory(self, memory: dict[str, Any]) -> str:
        if not memory.get("validated"):
            raise ValueError("Long-term memory must be validated")
        if contains_credential(memory.get("content")):
            raise ValueError("Long-term memory cannot contain credential data")
        data = redact_sensitive(memory)
        memory_id = data.get("id", str(uuid4()))
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO long_term_memories (
                    id, user_id, session_id, category, content_json, tags_json,
                    confidence, validated, provenance_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    data.get("user_id"),
                    data.get("session_id"),
                    data["category"],
                    _json_dump(data.get("content", {})),
                    _json_dump(data.get("tags", [])),
                    float(data.get("confidence", 1.0)),
                    1,
                    _json_dump(data.get("provenance", {})),
                    time.time(),
                ),
            )
        return memory_id

    def search_memories(
        self,
        user_id: str | None = None,
        session_id: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses = ["validated = 1"]
        params: list[Any] = []
        for column, value in (
            ("user_id", user_id),
            ("session_id", session_id),
            ("category", category),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        params.append(max(1, min(100, limit)))
        query = (
            "SELECT * FROM long_term_memories WHERE "
            + " AND ".join(clauses)
            + " ORDER BY confidence DESC, created_at DESC LIMIT ?"
        )
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        result = []
        required_tags = set(tags or [])
        for row in rows:
            item = dict(row)
            item["content"] = _json_load(item.pop("content_json"), {})
            item["tags"] = _json_load(item.pop("tags_json"), [])
            item["provenance"] = _json_load(item.pop("provenance_json"), {})
            item["validated"] = bool(item["validated"])
            if required_tags and not required_tags.issubset(set(item["tags"])):
                continue
            result.append(item)
        return result

    def add_routing_feedback(self, feedback: dict[str, Any]) -> str:
        data = redact_sensitive(feedback)
        feedback_id = data.get("id", str(uuid4()))
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO routing_feedback (
                    id, trace_id, query, selected_skill, corrected_skill,
                    accepted, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    data.get("trace_id"),
                    data["query"],
                    data.get("selected_skill"),
                    data.get("corrected_skill"),
                    int(bool(data.get("accepted", False))),
                    _json_dump(data.get("metadata", {})),
                    time.time(),
                ),
            )
        return feedback_id

    def get_routing_feedback(self, query: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM routing_feedback"
        params: tuple[Any, ...] = ()
        if query is not None:
            sql += " WHERE query = ?"
            params = (query,)
        sql += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = _json_load(item.pop("metadata_json"), {})
            item["accepted"] = bool(item["accepted"])
            result.append(item)
        return result
