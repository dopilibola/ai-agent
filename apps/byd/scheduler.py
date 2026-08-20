"""DealScheduler — the durable per-deal time scheduler (the funnel's keystone).

Implements the `sync.SyncJob` protocol, so `Runtime` runs it like anfa's KB sync:
every `interval_seconds` it claims the due `byd_scheduled_tasks` rows (concurrency-
safe, FOR UPDATE SKIP LOCKED), dispatches each through `funnel.execute_task`, and
marks it done — or, on error, retries it with backoff up to a cap before parking
it in `failed`. Claiming is bounded per poll (`batch_size`) so a post-downtime
backlog can't fan out a flood of Telegram sends at once.

Out-of-band by design: the executors send with NO inbound message and none of the
`current_channel`/`current_chat_id` context vars set — they reach the live channel
+ notifier through `funnel.get_context()`, whose handles are wired at startup.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from apps.byd.config import BydConfig, config as default_config
from apps.byd.funnel import execute_task
from apps.byd.repository import get_repository

logger = logging.getLogger(__name__)


@dataclass
class DealScheduler:
    """Polls + fires the funnel's scheduled tasks. Wire it into Tenant.sync_jobs."""

    cfg: BydConfig = default_config
    name: str = "byd-deal-scheduler"

    @property
    def interval_seconds(self) -> int:
        return self.cfg.scheduler_interval_seconds

    async def run_once(self) -> None:
        repo = get_repository()

        # Restart safety: recover tasks left 'running' by a crashed poller.
        try:
            reclaimed = await repo.reclaim_stale_running(older_than_seconds=600)
            if reclaimed:
                logger.warning("Reclaimed %d stale running BYD task(s)", reclaimed)
        except Exception:
            logger.exception("Failed to reclaim stale BYD tasks")

        try:
            tasks = await repo.claim_due_tasks(limit=self.cfg.scheduler_batch_size)
        except Exception:
            logger.exception("Failed to claim due BYD tasks")
            return

        if not tasks:
            return
        logger.info("BYD scheduler firing %d due task(s)", len(tasks))

        for task in tasks:
            try:
                await execute_task(task)
                await repo.mark_task_done(task.id)
            except Exception as exc:
                logger.exception(
                    "BYD task %s (%s) failed on attempt %d",
                    task.id, task.action_type, task.attempts,
                )
                try:
                    await repo.mark_task_retry_or_failed(
                        task.id,
                        error=repr(exc),
                        max_attempts=self.cfg.scheduler_max_attempts,
                    )
                except Exception:
                    logger.exception("Failed to record retry/failure for BYD task %s", task.id)
