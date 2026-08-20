"""Run the dedicated single-concurrency Temporal docking worker."""

from __future__ import annotations

import asyncio
import importlib.metadata
import signal
import socket
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from temporalio.client import Client

from src.task_runtime.config import TaskRuntimeConfig
from src.task_runtime.docking_execution import DockingExecution
from src.task_runtime.store import TaskStore
from src.task_runtime.staging import DockingInputStager
from src.task_runtime.temporal.activities import TemporalDockingActivities
from src.task_runtime.temporal.worker import (
    WorkerSettings,
    run_temporal_worker,
)


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except (NotImplementedError, RuntimeError):
            try:
                signal.signal(
                    signum,
                    lambda *_: loop.call_soon_threadsafe(stop.set),
                )
            except (OSError, ValueError):
                pass


async def main() -> None:
    config = TaskRuntimeConfig.from_env()
    client = await Client.connect(
        config.temporal_address,
        namespace=config.temporal_namespace,
    )
    store = TaskStore()
    activities = TemporalDockingActivities(
        store,
        DockingExecution(config.staging_root),
        stager=DockingInputStager(config.staging_root),
    )
    settings = WorkerSettings(
        task_queue=config.docking_queue,
        namespace=config.temporal_namespace,
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    await run_temporal_worker(
        client,
        activities,
        store,
        settings,
        worker_id=f"temporal-docking-{socket.gethostname()}",
        sdk_version=importlib.metadata.version("temporalio"),
        shutdown=stop,
    )


if __name__ == "__main__":
    asyncio.run(main())
