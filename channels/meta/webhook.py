"""One HTTPS endpoint for every Meta platform.

Telegram is a client that dials out and long-polls; Meta is the opposite — it
calls *us*, so a WhatsApp, Instagram or Messenger channel is not a transport
that runs, it is a handler that waits. This module is the part that runs: a
small ASGI app that Meta posts to, plus a registry mapping each platform in the
payload to the channel that should handle it.

**One app, one URL, many platforms.** Meta gives an app a single webhook URL and
sends every product's events to it, tagged by `object` ("whatsapp_business_account",
"instagram", "page"). So the server is shared and the channels register into it;
whichever channel `Runtime` starts first serves it, and the rest wait on the same
future rather than binding a second port.

**It runs inside the bot process, unlike the payments API.** A payment callback
only has to write a row, so it can live anywhere; an inbound message has to
reach a live `Agent` with its checkpointer and its mute store. Putting the
server in the same process is what makes that a function call instead of a
queue.

**The signature is mandatory.** This endpoint is on the public internet, and a
POST to it is indistinguishable from a real customer writing: forged traffic
would spend money on replies and poison the training corpus. So a request
without a valid `X-Hub-Signature-256` is refused, and if no app secret is
configured at all the endpoint refuses *everything* and says why — failing
closed, because the failure it would otherwise hide is someone else driving the
bot.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from typing import Any, Awaitable, Callable, Optional

# Module scope on purpose: with `from __future__ import annotations`, FastAPI
# resolves handler hints against these globals, and an import inside the factory
# silently turns every body parameter into a query parameter.
from fastapi import FastAPI, Header, HTTPException, Request, Response

logger = logging.getLogger(__name__)

# Which `object` value in a Meta payload belongs to which platform key.
OBJECT_TO_PLATFORM = {
    "whatsapp_business_account": "whatsapp",
    "instagram": "instagram",
    "page": "messenger",
}

Handler = Callable[[dict], Awaitable[None]]


class MetaWebhookServer:
    """The shared endpoint. Channels register a handler per platform."""

    def __init__(
        self,
        *,
        app_secret: str,
        verify_token: str,
        host: str = "127.0.0.1",
        port: int = 58240,
        path: str = "/meta/webhook",
    ) -> None:
        self.app_secret = (app_secret or "").strip()
        self.verify_token = (verify_token or "").strip()
        self.host = host
        self.port = port
        self.path = path
        self._handlers: dict[str, Handler] = {}
        self._started: Optional[asyncio.Future] = None

    # ----- registration --------------------------------------------------

    def register(self, platform: str, handler: Handler) -> None:
        self._handlers[platform] = handler

    # ----- lifecycle -----------------------------------------------------

    async def serve_once(self) -> None:
        """Run the server, or wait for the run somebody else started.

        Every Meta channel calls this from its own `run()`; only the first call
        binds the port. The others park on the same future so `Runtime` still
        sees one long-lived task per channel and shutdown behaves as usual.
        """
        loop = asyncio.get_running_loop()
        if self._started is not None:
            await self._started
            return
        self._started = loop.create_future()
        import uvicorn

        config = uvicorn.Config(
            self.build_app(),
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        logger.info(
            "Meta webhook listening on %s:%s%s for %s",
            self.host, self.port, self.path, ", ".join(sorted(self._handlers)) or "nothing",
        )
        # Shut down through uvicorn's own flag rather than by cancelling it.
        # Cancelling mid-serve makes uvicorn log a traceback on every restart,
        # and a log that cries wolf at every restart is a log nobody reads.
        task = asyncio.create_task(server.serve(), name="meta-webhook")
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            server.should_exit = True
            try:
                await asyncio.wait_for(task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
            logger.info("Meta webhook stopped")
            raise
        finally:
            if not self._started.done():
                self._started.set_result(None)

    # ----- the app -------------------------------------------------------

    def build_app(self) -> FastAPI:
        app = FastAPI(title="Meta webhook", docs_url=None, redoc_url=None)

        @app.get(self.path)
        async def verify(request: Request) -> Response:
            """Meta's one-time handshake: echo `hub.challenge` if the token matches."""
            params = request.query_params
            mode = params.get("hub.mode")
            token = params.get("hub.verify_token")
            challenge = params.get("hub.challenge") or ""
            if mode == "subscribe" and self.verify_token and token == self.verify_token:
                logger.info("Meta webhook verified")
                return Response(content=challenge, media_type="text/plain")
            logger.warning("Meta webhook verification refused (mode=%r)", mode)
            raise HTTPException(status_code=403, detail="verification failed")

        @app.post(self.path)
        async def receive(
            request: Request,
            x_hub_signature_256: str = Header(default=""),
        ) -> dict:
            raw = await request.body()
            if not self.app_secret:
                # Fail closed: without the secret we cannot tell Meta's traffic
                # from anyone else's, and "anyone else's" means a stranger can
                # make the bot talk to people and bill the account for it.
                logger.error(
                    "Meta webhook rejected a POST: MASKAN_META_APP_SECRET is not set"
                )
                raise HTTPException(status_code=503, detail="app secret not configured")
            if not self._signature_ok(raw, x_hub_signature_256):
                logger.warning("Meta webhook rejected a POST: bad signature")
                raise HTTPException(status_code=403, detail="bad signature")

            payload: dict[str, Any] = await request.json()
            platform = OBJECT_TO_PLATFORM.get(str(payload.get("object") or ""))
            handler = self._handlers.get(platform or "")
            if handler is None:
                logger.info("Meta webhook ignored object=%r", payload.get("object"))
                # 200 regardless: a non-200 makes Meta retry, and retrying
                # something we will never handle only fills the log.
                return {"ok": True, "ignored": True}
            # Handle out of band and answer immediately. Meta retries anything
            # it does not get an answer to within seconds, and an agent turn
            # takes longer than that — a slow reply would duplicate the message.
            asyncio.create_task(_guard(handler, payload))
            return {"ok": True}

        return app

    def _signature_ok(self, raw: bytes, header: str) -> bool:
        header = (header or "").strip()
        if not header.startswith("sha256="):
            return False
        expected = hmac.new(
            self.app_secret.encode("utf-8"), raw, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, header[len("sha256="):])


async def _guard(handler: Handler, payload: dict) -> None:
    """Run a handler without letting its failure kill the request task."""
    try:
        await handler(payload)
    except Exception:
        logger.exception("Meta webhook handler failed")
