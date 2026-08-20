"""FastAPI app factory + lifespan.

The lifespan opens one psycopg connection pool (mirroring `db.checkpointer_scope`
kwargs) and wraps it in an `AsyncPostgresSaver` for reading conversation
checkpoints; the same pool serves the `DISTINCT thread_id` enumeration. The
SQLAlchemy engine used by the mute/token stores is created lazily on first use
and disposed on shutdown. We do *not* call `saver.setup()` — the bots already
created the checkpoint tables and the panel is a read-only consumer of them.

The whole panel is a single process: the API is mounted under `/api`, and the
built React UI in `web/dist` is served at `/` (with SPA fallback). Build it with
`npm run build --prefix web`; responses read from disk per request, so a rebuild
is picked up without restarting the API.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from admin.config import config
from admin.repository import Repository
from admin.routers import (
    auth,
    catalog,
    chats,
    clinic,
    conversations,
    orders,
    prompts,
    tenants,
    usage,
)
from db.engine import database_configured, database_url, dispose_engine

logger = logging.getLogger(__name__)

# Built frontend bundle (repo_root/web/dist). Resolved from this file, not cwd.
_WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not database_configured():
        raise RuntimeError(
            "admin panel requires DATABASE_URL (Postgres); nothing to read otherwise."
        )

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(
        conninfo=database_url(),
        open=False,
        max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        saver = AsyncPostgresSaver(pool)
        app.state.repo = Repository(pool, saver)
        logger.info("admin panel ready on Postgres")
        try:
            yield
        finally:
            await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(title="ai-sales admin", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # All API routes live under /api so the SPA can own the rest of the paths.
    for module in (auth, tenants, chats, usage, conversations, catalog, orders, clinic, prompts):
        app.include_router(module.router, prefix="/api")

    @app.get("/api/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built React UI from web/dist at `/`, with SPA fallback.

    No-op (API-only) when the bundle hasn't been built yet. Registered last so
    the `/api/*` routes and FastAPI's own `/docs` keep precedence over the
    catch-all.
    """
    if not _WEB_DIST.is_dir():
        logger.warning(
            "web/dist not found — run `npm run build --prefix web`; serving API only"
        )
        return

    assets = _WEB_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index = _WEB_DIST / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        # Unknown /api/* paths should 404 as API, not fall back to the SPA.
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = _WEB_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)
