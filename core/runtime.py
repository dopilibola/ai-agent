"""Runtime — runs every channel and sync job in a tenant concurrently."""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Iterable, Union

from core.tenant import Tenant

logger = logging.getLogger(__name__)


class Runtime:
    """Owns a set of tenants and the event loop that drives them."""

    def __init__(self, tenants: Union[Tenant, Iterable[Tenant]]) -> None:
        if isinstance(tenants, Tenant):
            tenants = [tenants]
        self._tenants = list(tenants)

    async def run_async(self) -> None:
        tasks: list[asyncio.Task] = []
        for tenant in self._tenants:
            for channel in tenant.channels:
                tasks.append(
                    asyncio.create_task(
                        channel.run(),
                        name=f"{tenant.id}:channel:{channel.name}",
                    )
                )
            for job in tenant.sync_jobs:
                tasks.append(
                    asyncio.create_task(
                        _run_sync_job(job),
                        name=f"{tenant.id}:sync:{job.name}",
                    )
                )

        if not tasks:
            logger.warning("Runtime started with no tasks — nothing to do.")
            return

        stop = asyncio.Event()

        def _stop(*_: object) -> None:
            stop.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _stop)
            except NotImplementedError:
                pass  # windows / non-main thread

        # Each task runs independently. If one channel crashes (e.g. an
        # uninitialised userbot session), the others keep going — we only
        # tear down when SIGINT/SIGTERM fires or every task has exited.
        async def _watch(t: asyncio.Task) -> None:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Task %s exited with error", t.get_name())

        watcher_tasks = [asyncio.create_task(_watch(t)) for t in tasks]
        stop_task = asyncio.create_task(stop.wait(), name="stop")

        async def _all_finished() -> None:
            await asyncio.gather(*watcher_tasks)

        all_done = asyncio.create_task(_all_finished(), name="all-done")

        try:
            await asyncio.wait(
                [stop_task, all_done], return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            stop_task.cancel()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                *tasks, *watcher_tasks, all_done, return_exceptions=True
            )

    def run(self) -> None:
        asyncio.run(self.run_async())


async def _run_sync_job(job: "SyncJob") -> None:  # forward ref to avoid import
    while True:
        try:
            await job.run_once()
        except Exception:
            logger.exception("Sync job %s failed", job.name)
        await asyncio.sleep(job.interval_seconds)
