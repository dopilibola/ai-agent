"""Top-level help — point users at the per-tenant entrypoints."""

ENTRYPOINTS = """
ai-sales — multi-tenant Telegram AI sales platform

Tenant entrypoints (see pyproject.toml [project.scripts]):

  maskan-all           Run maskan userbot + operator bot + scheduler together
  maskan-userbot       Run only the maskan client userbot
  maskan-operator      Run only the maskan operator bot
  maskan-payments-api  Run the maskan payment-callback API

Helper scripts:
  python scripts/maskan_telethon_login.py
  python scripts/maskan_seed_catalog.py
"""


def main() -> None:
    print(ENTRYPOINTS)


if __name__ == "__main__":
    main()
