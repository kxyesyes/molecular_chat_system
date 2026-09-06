"""Run the Temporal SDK development server used by the docking canary."""

from __future__ import annotations

import asyncio
import signal

from temporalio.testing import WorkflowEnvironment


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
    environment = await WorkflowEnvironment.start_local(
        ip="127.0.0.1",
        port=7233,
        namespace="default",
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    try:
        await stop.wait()
    finally:
        await environment.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
