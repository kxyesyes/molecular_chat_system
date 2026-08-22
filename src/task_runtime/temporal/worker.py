"""Dedicated Temporal worker lifecycle for docking tasks."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from temporalio.worker import Worker

from src.task_runtime.prometheus_metrics import TemporalWorkerMetrics
from src.task_runtime.store import TaskStore

from .activities import TemporalDockingActivities
from .workflows import DockingWorkflow


logger = logging.getLogger(__name__)
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")


def _best_effort_metric(
    metrics: TemporalWorkerMetrics | None,
    method: str,
    *args: Any,
) -> None:
    if metrics is None:
        return
    try:
        getattr(metrics, method)(*args)
    except Exception:
        pass


@dataclass(frozen=True)
class WorkerSettings:
    task_queue: str
    namespace: str
    max_concurrent_activities: int = 1
    heartbeat_interval: float = 5.0
    graceful_shutdown_seconds: float = 30.0

    def __post_init__(self) -> None:
        if _SAFE_NAME.fullmatch(self.task_queue or "") is None:
            raise ValueError("invalid task queue")
        if _SAFE_NAME.fullmatch(self.namespace or "") is None:
            raise ValueError("invalid namespace")
        if self.max_concurrent_activities != 1:
            raise ValueError("docking activity concurrency must be one")
        if not 0 < self.heartbeat_interval <= 5:
            raise ValueError("invalid heartbeat interval")
        if not 0 < self.graceful_shutdown_seconds <= 60:
            raise ValueError("invalid graceful shutdown timeout")


def build_temporal_worker(
    client,
    activities: TemporalDockingActivities,
    settings: WorkerSettings,
) -> Worker:
    return Worker(
        client,
        task_queue=settings.task_queue,
        workflows=[DockingWorkflow],
        activities=[
            activities.verify_manifest_activity,
            activities.run_docking_activity,
            activities.project_task_activity,
        ],
        max_concurrent_activities=1,
        max_heartbeat_throttle_interval=timedelta(seconds=5),
        default_heartbeat_throttle_interval=timedelta(seconds=1),
        graceful_shutdown_timeout=timedelta(
            seconds=settings.graceful_shutdown_seconds
        ),
    )


async def run_worker_heartbeat(
    store: TaskStore,
    *,
    worker_id: str,
    task_queue: str,
    sdk_version: str,
    stop: asyncio.Event,
    interval: float = 5.0,
    metrics: TemporalWorkerMetrics | None = None,
    backup_state_path: Path | None = None,
) -> None:
    if not 0 < interval <= 5:
        raise ValueError("invalid worker heartbeat interval")
    while not stop.is_set():
        try:
            await asyncio.to_thread(
                store.record_worker_heartbeat,
                worker_id,
                backend="temporal",
                task_queue=task_queue,
                concurrency=1,
                sdk_version=sdk_version,
            )
        except Exception:
            logger.error("worker_heartbeat_failed code=TASK_PROJECTION_FAILED")
        _best_effort_metric(metrics, "record_worker_heartbeat")
        if backup_state_path is not None:
            _best_effort_metric(
                metrics,
                "refresh_backup_verification",
                backup_state_path,
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def run_temporal_worker(
    client,
    activities: TemporalDockingActivities,
    store: TaskStore,
    settings: WorkerSettings,
    *,
    worker_id: str,
    sdk_version: str,
    shutdown: asyncio.Event,
    metrics: TemporalWorkerMetrics | None = None,
    backup_state_path: Path | None = None,
) -> None:
    worker = build_temporal_worker(client, activities, settings)
    heartbeat_stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        run_worker_heartbeat(
            store,
            worker_id=worker_id,
            task_queue=settings.task_queue,
            sdk_version=sdk_version,
            stop=heartbeat_stop,
            interval=settings.heartbeat_interval,
            metrics=metrics,
            backup_state_path=backup_state_path,
        )
    )

    async def poll() -> None:
        _best_effort_metric(metrics, "set_ready", True)
        try:
            await worker.run()
        finally:
            _best_effort_metric(metrics, "set_ready", False)

    worker_task = asyncio.create_task(poll())
    shutdown_task = asyncio.create_task(shutdown.wait())
    logger.info(
        "temporal_worker_started queue=%s namespace=%s sdk_version=%s",
        settings.task_queue,
        settings.namespace,
        sdk_version,
    )
    try:
        done, _ = await asyncio.wait(
            {worker_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if shutdown_task in done and not worker_task.done():
            await worker.shutdown()
        await worker_task
    except asyncio.CancelledError:
        async def stop_polling() -> None:
            if not worker_task.done():
                try:
                    await asyncio.wait_for(
                        worker.shutdown(),
                        timeout=settings.graceful_shutdown_seconds,
                    )
                except Exception:
                    worker_task.cancel()
            if not worker_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(worker_task),
                        timeout=settings.graceful_shutdown_seconds,
                    )
                except Exception:
                    worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)

        cleanup_task = asyncio.create_task(stop_polling())
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                continue
        cleanup_task.result()
        raise
    finally:
        shutdown_task.cancel()
        await asyncio.gather(shutdown_task, return_exceptions=True)
        heartbeat_stop.set()
        await heartbeat_task
        logger.info(
            "temporal_worker_stopped queue=%s namespace=%s sdk_version=%s",
            settings.task_queue,
            settings.namespace,
            sdk_version,
        )


__all__ = [
    "WorkerSettings",
    "build_temporal_worker",
    "run_temporal_worker",
    "run_worker_heartbeat",
]
