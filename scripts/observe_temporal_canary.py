"""Build sanitized, read-only Temporal canary observation evidence."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.manage_temporal_canary import (  # noqa: E402
    _StableArgumentParser,
    _is_reparse_point,
    _safe_read_snapshot,
)
from src.task_runtime.config import (  # noqa: E402
    TaskRuntimeConfig,
    read_temporal_backup_state,
)
from src.task_runtime.deployment_contract import (  # noqa: E402
    TEMPORAL_RELEASE_BLOCKER_NAMES,
)
from src.task_runtime.models import (  # noqa: E402
    TaskStatus,
    canonical_artifact_type,
    is_safe_relative_artifact_path,
    sanitize_public_artifacts,
    sanitize_public_provenance,
    sanitize_task_result_projection,
)
from src.task_runtime.observation import (  # noqa: E402
    ObservationPolicy,
    build_observation_report,
)
from src.task_runtime.secure_io import write_json_atomic  # noqa: E402
from src.task_runtime.store import (  # noqa: E402
    ReadOnlyTemporalObservationStore,
)
from src.task_runtime.trusted_files import (  # noqa: E402
    _capture_trusted_path_boundary,
    _normalized_path,
    _path_identities,
)


MAX_PROMETHEUS_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_REPORT_BYTES = 4 * 1024 * 1024 + 4096
PROMETHEUS_TIMEOUT_SECONDS = 5.0
BACKUP_MAX_AGE = timedelta(hours=24)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ARTIFACT_SHA256 = re.compile(r"[0-9A-Fa-f]{64}\Z")
_SAFE_TASK_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,254}\Z")
_BLOCKING_ALERT_NAMES = TEMPORAL_RELEASE_BLOCKER_NAMES
PROMETHEUS_QUERIES = {
    "worker_ready": "max(medchat_temporal_worker_ready)",
    "worker_age_seconds": (
        "time() - max(medchat_temporal_worker_last_heartbeat_timestamp_seconds)"
    ),
    "temporal": 'min(up{job="temporal-server"})',
    "namespace": (
        'count(service_requests{namespace="default"}) > bool 0'
    ),
    "queue": (
        'count(poll_success{namespace="default",taskqueue="medchat-docking"}) '
        "> bool 0"
    ),
}


class PrometheusUnavailable(ValueError):
    """A non-sensitive indication that monitoring evidence is unavailable."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_prometheus_url(value: str) -> str:
    if type(value) is not str or not value or len(value) > 256:
        raise ValueError("invalid Prometheus URL")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise ValueError("invalid Prometheus URL") from None
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != ""
        or parsed.query
        or parsed.fragment
        or not parsed.netloc
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("invalid Prometheus URL")
    expected_netloc = (
        f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
    )
    if port is not None:
        expected_netloc += f":{port}"
    if parsed.netloc != expected_netloc:
        raise ValueError("invalid Prometheus URL")
    return value


def _decode_json_object(content: bytes, message: str) -> dict[str, object]:
    try:
        text = content.decode("utf-8")
        if "\x00" in text:
            raise ValueError

        def pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError
                result[key] = item
            return result

        payload = json.loads(text, object_pairs_hook=pairs_hook)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise ValueError(message) from None
    if type(payload) is not dict:
        raise ValueError(message)
    return payload


def _decode_prometheus_json(content: bytes) -> dict[str, object]:
    if type(content) is not bytes or len(content) > MAX_PROMETHEUS_BYTES:
        raise PrometheusUnavailable("monitoring unavailable")
    try:
        return _decode_json_object(content, "monitoring unavailable")
    except ValueError:
        raise PrometheusUnavailable("monitoring unavailable") from None


def _default_opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
        urllib.request.HTTPHandler(),
    )


def _request_prometheus_json(
    base_url: str,
    endpoint: str,
    query: str | None = None,
    *,
    opener=None,
) -> dict[str, object]:
    base = validate_prometheus_url(base_url)
    if endpoint not in {"alerts", "query"} or (endpoint == "query") != (query is not None):
        raise PrometheusUnavailable("monitoring unavailable")
    url = f"{base}/api/v1/{endpoint}"
    if query is not None:
        url += "?" + urllib.parse.urlencode({"query": query})
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "MedChat-Canary-Observer/1"},
        method="GET",
    )
    client = opener or _default_opener()
    try:
        with client.open(request, timeout=PROMETHEUS_TIMEOUT_SECONDS) as response:
            status_code = getattr(response, "status", response.getcode())
            if status_code != 200 or response.geturl() != url:
                raise PrometheusUnavailable("monitoring unavailable")
            content_type = response.headers.get("Content-Type")
            if (
                type(content_type) is not str
                or content_type.split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                raise PrometheusUnavailable("monitoring unavailable")
            raw_length = response.headers.get("Content-Length")
            if raw_length is not None:
                try:
                    length = int(raw_length)
                except (TypeError, ValueError):
                    raise PrometheusUnavailable("monitoring unavailable") from None
                if length < 0 or length > MAX_PROMETHEUS_BYTES:
                    raise PrometheusUnavailable("monitoring unavailable")
            content = response.read(MAX_PROMETHEUS_BYTES + 1)
    except PrometheusUnavailable:
        raise
    except Exception:
        raise PrometheusUnavailable("monitoring unavailable") from None
    return _decode_prometheus_json(content)


def _project_alerts(payload: object) -> list[str]:
    if type(payload) is not dict or set(payload) != {"status", "data"}:
        raise PrometheusUnavailable("monitoring unavailable")
    if payload.get("status") != "success":
        raise PrometheusUnavailable("monitoring unavailable")
    data = payload.get("data")
    if type(data) is not dict or set(data) != {"alerts"}:
        raise PrometheusUnavailable("monitoring unavailable")
    alerts = data.get("alerts")
    if type(alerts) is not list or len(alerts) > 1000:
        raise PrometheusUnavailable("monitoring unavailable")
    blocking: set[str] = set()
    allowed_alert_fields = {"labels", "annotations", "state", "activeAt", "value"}
    for alert in alerts:
        if type(alert) is not dict or not set(alert) <= allowed_alert_fields:
            raise PrometheusUnavailable("monitoring unavailable")
        labels = alert.get("labels")
        state = alert.get("state")
        if type(labels) is not dict or state not in {"pending", "firing", "inactive"}:
            raise PrometheusUnavailable("monitoring unavailable")
        annotations = alert.get("annotations")
        if annotations is not None and (
            type(annotations) is not dict
            or len(annotations) > 64
            or not all(
                type(key) is str
                and type(item) is str
                and len(key) <= 128
                and len(item) <= 2048
                for key, item in annotations.items()
            )
        ):
            raise PrometheusUnavailable("monitoring unavailable")
        for optional_name in ("activeAt", "value"):
            optional_value = alert.get(optional_name)
            if optional_value is not None and (
                type(optional_value) is not str or len(optional_value) > 256
            ):
                raise PrometheusUnavailable("monitoring unavailable")
        if len(labels) > 64 or not all(
            type(key) is str
            and type(item) is str
            and len(key) <= 128
            and len(item) <= 512
            for key, item in labels.items()
        ):
            raise PrometheusUnavailable("monitoring unavailable")
        name = labels.get("alertname")
        if state == "firing" and name in _BLOCKING_ALERT_NAMES:
            blocking.add(name)
    return sorted(blocking)


def _project_query_number(payload: object) -> float:
    if type(payload) is not dict or set(payload) != {"status", "data"}:
        raise PrometheusUnavailable("monitoring unavailable")
    if payload.get("status") != "success":
        raise PrometheusUnavailable("monitoring unavailable")
    data = payload.get("data")
    if type(data) is not dict or set(data) != {"resultType", "result"}:
        raise PrometheusUnavailable("monitoring unavailable")
    result = data.get("result")
    if data.get("resultType") != "vector" or type(result) is not list or len(result) != 1:
        raise PrometheusUnavailable("monitoring unavailable")
    item = result[0]
    if type(item) is not dict or set(item) != {"metric", "value"} or item.get("metric") != {}:
        raise PrometheusUnavailable("monitoring unavailable")
    value = item.get("value")
    if type(value) is not list or len(value) != 2 or type(value[1]) is not str:
        raise PrometheusUnavailable("monitoring unavailable")
    try:
        timestamp = float(value[0])
        number = float(value[1])
    except (OverflowError, TypeError, ValueError):
        raise PrometheusUnavailable("monitoring unavailable") from None
    if not math.isfinite(timestamp) or not math.isfinite(number):
        raise PrometheusUnavailable("monitoring unavailable")
    return number


def _project_query_bool(payload: object) -> bool:
    number = _project_query_number(payload)
    if number not in {0.0, 1.0}:
        raise PrometheusUnavailable("monitoring unavailable")
    return bool(number)


def _prometheus_state(
    fetcher: Callable[[str, str | None], dict[str, object]],
) -> tuple[dict[str, object], dict[str, object], list[str] | None]:
    try:
        alerts: list[str] | None = _project_alerts(fetcher("alerts", None))
    except Exception:
        alerts = None

    values: dict[str, bool | float | None] = {}
    for name, query in PROMETHEUS_QUERIES.items():
        try:
            payload = fetcher("query", query)
            values[name] = (
                _project_query_number(payload)
                if name == "worker_age_seconds"
                else _project_query_bool(payload)
            )
        except Exception:
            values[name] = None
    age = values["worker_age_seconds"]
    if type(age) is float and age < 0:
        age = None
    worker = {"available": values["worker_ready"], "age_seconds": age}
    infrastructure = {
        "temporal": values["temporal"],
        "namespace": values["namespace"],
        "queue": values["queue"],
    }
    return worker, infrastructure, alerts


def _aware_utc(value: datetime, message: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(message)
    try:
        return value.astimezone(timezone.utc)
    except (OSError, OverflowError, ValueError):
        raise ValueError(message) from None


def _parse_time(value: str, message: str) -> datetime:
    if type(value) is not str or len(value) > 128:
        raise ValueError(message)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError(message) from None
    return _aware_utc(parsed, message)


def read_backup_verification(path: Path, *, now: datetime) -> bool | None:
    checked_at = _aware_utc(now, "invalid backup clock")
    try:
        lexical = _normalized_path(path)
        boundary = _capture_trusted_path_boundary(lexical)
        content = read_temporal_backup_state(lexical)
        if _capture_trusted_path_boundary(lexical) != boundary:
            raise ValueError("invalid backup marker")
        payload = _decode_json_object(content, "invalid backup marker")
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    status_value = payload.get("status")
    if status_value == "failed":
        return False
    if status_value != "passed":
        return None
    digest = payload.get("sha256")
    verified_at = payload.get("verified_at")
    if type(digest) is not str or _SHA256.fullmatch(digest) is None or type(verified_at) is not str:
        return None
    try:
        verified = _parse_time(verified_at, "invalid backup timestamp")
    except ValueError:
        return None
    if verified > checked_at or checked_at - verified > BACKUP_MAX_AGE:
        return None
    return True


def _task_staging_root() -> Path:
    return TaskRuntimeConfig.from_env().staging_root


def _safe_status(value: object) -> str:
    if isinstance(value, TaskStatus):
        status_value = value.value
    else:
        status_value = getattr(value, "value", value)
    if status_value not in {"succeeded", "failed", "canceled", "timed_out"}:
        raise ValueError("invalid task observation")
    return str(status_value)


def _artifact_evidence(
    record: object,
    raw_result: object,
    *,
    staging_root: Path,
) -> tuple[bool, bool]:
    task_id = getattr(record, "task_id", None)
    if type(task_id) is not str or _SAFE_TASK_COMPONENT.fullmatch(task_id) is None:
        return False, False
    artifacts = getattr(record, "artifacts", None)
    if not isinstance(artifacts, list):
        return False, False
    claimed_pose_artifacts = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict)
        and (
            artifact.get("artifact_type") == "docking_pose"
            or artifact.get("type") == "docking_pose"
        )
    ]
    if len(claimed_pose_artifacts) != 1 or canonical_artifact_type(
        claimed_pose_artifacts[0]
    ) != "docking_pose":
        return False, False
    source_artifact = claimed_pose_artifacts[0]
    expected_size: int | None = None
    if "size" in source_artifact:
        candidate_size = source_artifact["size"]
        if type(candidate_size) is not int or candidate_size < 0:
            return False, False
        expected_size = candidate_size
    safe_artifacts = sanitize_public_artifacts(artifacts)
    pose_artifacts = [
        artifact
        for artifact in safe_artifacts
        if canonical_artifact_type(artifact) == "docking_pose"
    ]
    if len(pose_artifacts) != 1:
        return False, False
    artifact = pose_artifacts[0]
    if not isinstance(raw_result, Mapping):
        return False, False
    # SCIENTIFIC_STRICT intentionally drops pose_file; the persisted, sanitized
    # docking_pose artifact is the only path authority available to observation.
    artifact_path = artifact.get("path")
    if (
        type(artifact_path) is not str
        or not is_safe_relative_artifact_path(artifact_path)
    ):
        return False, False

    artifact_digests: list[str] = []
    for key in ("sha256", "hash"):
        if key not in source_artifact:
            continue
        value = source_artifact[key]
        if type(value) is not str or _ARTIFACT_SHA256.fullmatch(value) is None:
            return False, False
        artifact_digests.append(value.lower())
    if not artifact_digests or len(set(artifact_digests)) != 1:
        return False, False
    digest = artifact_digests[0]

    result_digests: list[str] = []
    completion = raw_result.get("completion")
    if completion is not None:
        if not isinstance(completion, Mapping):
            return False, False
        scopes = [completion]
    else:
        scopes = []
    for scope in scopes:
        for key in (
            "pose_sha256",
            "sha256",
            "hash",
            "output_hash",
            "digest",
        ):
            if key not in scope:
                continue
            value = scope[key]
            if type(value) is not str or _ARTIFACT_SHA256.fullmatch(value) is None:
                return False, False
            result_digests.append(value.lower())
    if any(value != digest for value in result_digests):
        return False, False

    if expected_size is not None and artifact.get("size") != expected_size:
        return False, False
    root = _normalized_path(staging_root)
    artifact_file = root / task_id / Path(*artifact_path.split("/"))
    try:
        snapshot = _safe_read_snapshot(artifact_file, MAX_ARTIFACT_BYTES)
    except ValueError:
        return False, False
    # Stable metadata guards the read; the digest detects same-size in-place
    # content changes that filesystem version fields alone cannot authenticate.
    actual = hashlib.sha256(snapshot.content).hexdigest()
    size_matches = expected_size is None or len(snapshot.content) == expected_size
    return True, actual == digest and size_matches


def _project_record(record: object, store: object, *, staging_root: Path) -> dict[str, object]:
    task_id = getattr(record, "task_id", None)
    workflow_id = getattr(record, "external_workflow_id", None)
    if type(task_id) is not str or type(workflow_id) is not str:
        raise ValueError("invalid task observation")
    status_value = _safe_status(getattr(record, "status", None))
    raw_result = getattr(record, "result", None)
    result = sanitize_task_result_projection(raw_result)
    if not isinstance(result, dict):
        result = {}
    raw_provenance = getattr(record, "provenance", None)
    provenance = sanitize_public_provenance(raw_provenance)
    attempt = getattr(record, "attempt", None)
    if type(attempt) is not int or attempt < 0:
        raise ValueError("invalid task observation")
    started = _parse_time(getattr(record, "started_at", None), "invalid task observation")
    finished = _parse_time(getattr(record, "finished_at", None), "invalid task observation")
    latency = (finished - started).total_seconds()
    if latency < 0 or not math.isfinite(latency):
        raise ValueError("invalid task observation")
    terminal_count = store.terminal_event_count(task_id)
    if type(terminal_count) is not int or terminal_count < 0:
        raise ValueError("invalid task observation")

    pose_count = result.get("pose_count")
    if type(pose_count) is not int or pose_count < 0:
        pose_count = 0
    energy = result.get("best_energy")
    energy_valid = type(energy) in {int, float}
    if energy_valid:
        try:
            energy_valid = math.isfinite(float(energy))
        except (OverflowError, TypeError, ValueError):
            energy_valid = False
    artifact_exists, artifact_hash_matches = _artifact_evidence(
        record, raw_result, staging_root=staging_root
    )
    provenance_available = isinstance(raw_provenance, dict) and (
        type(raw_provenance.get("tool_name")) is str
        and type(raw_provenance.get("tool_version")) is str
        and type(raw_provenance.get("demo_mode")) is bool
        and type(raw_provenance.get("fallback_used")) is bool
    )
    if provenance_available:
        provenance_complete: bool | None = (
            provenance.get("tool_name") == "molecular_docking"
            and type(provenance.get("tool_version")) is str
            and bool(provenance.get("tool_version"))
            and provenance.get("demo_mode") is False
            and provenance.get("fallback_used") is False
        )
        demo_mode = provenance.get("demo_mode")
        fallback_used = provenance.get("fallback_used")
    else:
        provenance_complete = None
        demo_mode = None
        fallback_used = None
    error_code = getattr(record, "error_code", None)
    if hasattr(error_code, "value"):
        error_code = error_code.value
    return {
        "task_fingerprint": task_id,
        "workflow_fingerprint": workflow_id,
        "status": status_value,
        "attempt": attempt,
        "terminal_event_count": terminal_count,
        "pose_count": pose_count,
        "binding_energy": energy if energy_valid else None,
        "binding_energy_valid": energy_valid,
        "artifact_exists": artifact_exists,
        "artifact_hash_matches": artifact_hash_matches,
        "provenance_complete": provenance_complete,
        "demo_mode": demo_mode,
        "fallback_used": fallback_used,
        "latency_seconds": latency,
        "error_code": error_code,
    }


def _safe_existing_database(path: Path) -> Path:
    lexical = _normalized_path(path)
    identities = _path_identities(lexical)
    metadata = lexical.lstat()
    if _is_reparse_point(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("task evidence unavailable")
    if identities != _path_identities(lexical):
        raise ValueError("task evidence unavailable")
    return lexical


def collect_observation(
    *,
    db_path: Path,
    current_level: int,
    window_start: datetime,
    window_end: datetime,
    prometheus_url: str,
    backup_state: Path,
    baseline_p95_seconds: float,
    now: datetime | None = None,
    observation_store_factory: Callable[[Path], object] = (
        ReadOnlyTemporalObservationStore
    ),
    prometheus_fetcher: Callable[[str, str | None], dict[str, object]] | None = None,
) -> dict[str, object]:
    generated = _aware_utc(now or datetime.now(timezone.utc), "invalid observation clock")
    start = _aware_utc(window_start, "invalid observation window")
    end = _aware_utc(window_end, "invalid observation window")
    base_url = validate_prometheus_url(prometheus_url)
    policy = ObservationPolicy(baseline_p95_seconds=baseline_p95_seconds)
    fetcher = prometheus_fetcher or (
        lambda endpoint, query=None: _request_prometheus_json(
            base_url, endpoint, query
        )
    )
    worker, infrastructure, alerts = _prometheus_state(fetcher)
    backup_verified = read_backup_verification(backup_state, now=generated)

    samples: list[dict[str, object]] = []
    try:
        store_path = (
            _safe_existing_database(db_path)
            if observation_store_factory is ReadOnlyTemporalObservationStore
            else _normalized_path(db_path)
        )
        with observation_store_factory(store_path) as store:
            records = store.list_temporal_observation(start, end, limit=10_000)
            if not isinstance(records, list):
                raise ValueError("task evidence unavailable")
            staging_root = _task_staging_root()
            samples = [
                _project_record(record, store, staging_root=staging_root)
                for record in records
            ]
    except Exception:
        samples = []

    return build_observation_report(
        samples,
        current_level=current_level,
        window_start=start,
        window_end=end,
        worker_health=worker,
        infrastructure=infrastructure,
        blocking_alerts=alerts,
        backup_verified=backup_verified,
        policy=policy,
        generated_at=generated,
    )


def write_report_atomic(path: Path, report: Mapping[str, object]) -> None:
    write_json_atomic(
        path,
        report,
        maximum_bytes=MAX_REPORT_BYTES,
        capture_boundary=_capture_trusted_path_boundary,
        is_reparse=_is_reparse_point,
    )


def _parser() -> argparse.ArgumentParser:
    parser = _StableArgumentParser(prog="observe_temporal_canary.py", add_help=True)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--level", type=int, choices=(5, 10, 25), required=True)
    parser.add_argument("--window-start", required=True)
    parser.add_argument("--window-end", required=True)
    parser.add_argument("--prometheus-url", required=True)
    parser.add_argument("--backup-state", type=Path, required=True)
    parser.add_argument("--baseline-p95-seconds", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        report = collect_observation(
            db_path=args.db_path,
            current_level=args.level,
            window_start=_parse_time(args.window_start, "invalid observation window"),
            window_end=_parse_time(args.window_end, "invalid observation window"),
            prometheus_url=args.prometheus_url,
            backup_state=args.backup_state,
            baseline_p95_seconds=args.baseline_p95_seconds,
        )
        write_report_atomic(args.output, report)
        print(
            f"status={report['status']} sample_count={report['sample_count']} "
            f"current_level={report['current_level']} sha256={report['sha256']}"
        )
        return 0
    except SystemExit as exc:
        return int(exc.code)
    except (MemoryError, OSError, RuntimeError, TypeError, ValueError):
        print("error: observation_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
