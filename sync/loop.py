"""SyncJob — a background task that runs every `interval_seconds`.

The Runtime owns the scheduling loop; concrete jobs implement `run_once()`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SyncJob(Protocol):
    name: str
    interval_seconds: int

    async def run_once(self) -> None: ...
