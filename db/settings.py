"""Operator-editable knobs, read by the bots and written by the admin panel.

The panel writes a `panel_settings` row; the bot process reads it here through a
short TTL cache, so a switch flipped in the browser reaches the running
scheduler within `_TTL` seconds without a restart — the same live-edit contract
the prompt files already have, for the settings that have no file.

Lazy imports, like the rest of `db/`: importing this module costs nothing until
a setting is actually read.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Short enough that an operator flipping a switch sees it take effect while they
# are still looking at the panel; long enough that a scheduler tick polling many
# chats does not hit the database once per chat.
_TTL = 20.0

_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def invalidate(tenant_id: str = "", key: str = "") -> None:
    """Drop cached values. No arguments clears everything."""
    if not tenant_id and not key:
        _cache.clear()
        return
    _cache.pop((tenant_id, key), None)


async def get_setting(tenant_id: str, key: str, default: dict | None = None) -> dict:
    """Read one setting, falling back to the all-tenants row and then `default`.

    Never raises: a database hiccup returns the default, because a knob that
    cannot be read must not stop the funnel from working the way it did before
    anyone touched the panel.
    """
    fallback = dict(default or {})
    now = time.monotonic()
    hit = _cache.get((tenant_id, key))
    if hit and hit[0] > now:
        return {**fallback, **hit[1]}

    stored: dict = {}
    try:
        from sqlalchemy import select

        from db.engine import database_configured, get_sessionmaker
        from db.models import PanelSetting

        if database_configured():
            async with get_sessionmaker()() as session:
                rows = (
                    await session.execute(
                        select(PanelSetting).where(
                            PanelSetting.key == key,
                            PanelSetting.tenant_id.in_(("", tenant_id)),
                        )
                    )
                ).scalars().all()
            # The tenant's own row wins over the all-tenants default.
            by_tenant = {r.tenant_id: (r.value or {}) for r in rows}
            stored = {**by_tenant.get("", {}), **by_tenant.get(tenant_id, {})}
    except Exception:
        logger.debug("panel setting %s/%s unreadable", tenant_id, key, exc_info=True)
        stored = hit[1] if hit else {}

    _cache[(tenant_id, key)] = (now + _TTL, stored)
    return {**fallback, **stored}


async def set_setting(tenant_id: str, key: str, value: dict[str, Any]) -> None:
    """Write one setting (upsert) and drop its cache entry."""
    from datetime import datetime, timezone

    from sqlalchemy.dialects.postgresql import insert

    from db.engine import get_sessionmaker
    from db.models import PanelSetting

    stmt = insert(PanelSetting).values(
        tenant_id=tenant_id,
        key=key,
        value=dict(value),
        updated_at=datetime.now(timezone.utc),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[PanelSetting.tenant_id, PanelSetting.key],
        set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
    )
    async with get_sessionmaker()() as session:
        await session.execute(stmt)
        await session.commit()
    invalidate(tenant_id, key)
