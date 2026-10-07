from __future__ import annotations

import asyncio

from src.task_runtime.runtime import TaskRuntimeBinding


class _Router:
    def __init__(self) -> None:
        self.handlers: dict[str, list[object]] = {"startup": [], "shutdown": []}

    def add_event_handler(self, event: str, handler) -> None:
        self.handlers[event].append(handler)


class _App:
    def __init__(self) -> None:
        self.router = _Router()


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str) -> None:
        self.warnings.append(message)


class _Runtime:
    def __init__(self) -> None:
        self.cleanup_calls = 0
        self.shutdown_calls = 0

    async def cleanup_staging_once(self) -> list[str]:
        self.cleanup_calls += 1
        return []


async def _run_startup(binding: TaskRuntimeBinding, app: _App) -> None:
    await app.router.handlers["startup"][0]()


def test_binding_cleans_expired_staging_when_runtime_starts() -> None:
    runtime = _Runtime()
    app = _App()
    logger = _Logger()
    binding = TaskRuntimeBinding(factory=lambda: runtime, shutdown=lambda _: asyncio.sleep(0))
    binding.install(app, logger)

    asyncio.run(_run_startup(binding, app))

    assert runtime.cleanup_calls == 1
    assert logger.warnings == []
