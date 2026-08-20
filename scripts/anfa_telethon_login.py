"""One-time interactive Telethon login for the anfa userbot.

Prompts for the SMS code Telegram sends to ANFA_USERBOT_PHONE (and the 2FA
password if the account has one). Writes the session to ANFA_USERBOT_SESSION;
after that `anfa-userbot` can connect non-interactively.

Usage:
    uv run python scripts/anfa_telethon_login.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

from apps.anfa.config import config  # noqa: E402


async def main() -> None:
    if not config.api_id or not config.api_hash or not config.userbot_phone:
        print(
            "Set TG_API_ID, TG_API_HASH, and ANFA_USERBOT_PHONE in .env first.",
            file=sys.stderr,
        )
        sys.exit(1)

    Path(config.userbot_session).parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(config.userbot_session, config.api_id, config.api_hash)
    await client.start(phone=config.userbot_phone)
    me = await client.get_me()
    print(
        f"Logged in as {getattr(me, 'username', None) or me.first_name} (id={me.id}). "
        f"Session saved to {config.userbot_session}.session"
    )
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
