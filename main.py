"""Top-level help — point users at the per-tenant entrypoints."""

ENTRYPOINTS = """
ai-sales — multi-tenant Telegram AI sales platform

Tenant entrypoints (see pyproject.toml [project.scripts]):

  oygul-customer   Run the flower-shop customer agent (Lola) on a user account
  oygul-merchant   Run the flower-shop merchant catalog bot
  anfa-all        Run anfa bot + userbot + KB sync together
  anfa-bot        Run only the anfa booking bot
  anfa-userbot    Run only the anfa userbot
  anfa-sync       Run only the anfa CRM → vector DB sync loop

Helper scripts:
  python scripts/oygul_embed.py --json bouquets.json
  python scripts/anfa_telethon_login.py
"""


def main() -> None:
    print(ENTRYPOINTS)


if __name__ == "__main__":
    main()
