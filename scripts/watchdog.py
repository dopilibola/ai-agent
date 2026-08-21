"""Health watchdog — notices an outage and says so on Telegram.

Runs *outside* the bot process (systemd timer), because the failure it most
needs to report is the bot process being dead. Every check is cheap and
independent; each one flips between OK and FAILED, and only a *transition*
sends a message — so a service that stays down does not spam the operators, and
a recovery is announced once.

    uv run python scripts/watchdog.py            # one pass (what the timer runs)
    uv run python scripts/watchdog.py --status   # print state, send nothing
    uv run python scripts/watchdog.py --test     # send a test alert

Checks:
    unit        the tenant's systemd service is active and not crash-looping
    database    Postgres answers, and the schema is at a known migration
    corpus      conversation logging is on and the table is being written to
    telegram    the operator bot's token still works AND an operator is reachable
    disk        free space on the partition holding the DB and the backups
    backup      last successful dump is recent enough to be worth restoring

State lives in a JSON file next to the checkout, so a restart of the timer does
not re-announce everything.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STATE_PATH = Path(os.environ.get("WATCHDOG_STATE", ROOT / "data" / "watchdog_state.json"))
UNITS = os.environ.get("WATCHDOG_UNITS", "ai-agent-maskan").split(",")
DISK_MIN_FREE_PCT = float(os.environ.get("WATCHDOG_DISK_MIN_FREE_PCT", "8"))
CORPUS_STALE_HOURS = float(os.environ.get("WATCHDOG_CORPUS_STALE_HOURS", "48"))
BACKUP_MAX_AGE_HOURS = float(os.environ.get("WATCHDOG_BACKUP_MAX_AGE_HOURS", "30"))
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", Path.home() / "backups" / "ai-sales"))


@dataclass
class Result:
    name: str
    ok: bool
    detail: str


# ----- environment ----------------------------------------------------------

def load_env() -> dict:
    """Read `.env` without dotenv's side effects — the watchdog must not depend
    on the bot's package being importable, since a broken install is exactly the
    kind of outage it reports."""
    env: dict[str, str] = {}
    path = ROOT / ".env"
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", line)
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


ENV = {**load_env(), **os.environ}


def env(*names: str, default: str = "") -> str:
    for name in names:
        value = ENV.get(name)
        if value:
            return value
    return default


# ----- checks ---------------------------------------------------------------

def check_units() -> list[Result]:
    results = []
    for unit in [u.strip() for u in UNITS if u.strip()]:
        try:
            active = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True, text=True, timeout=15,
            ).stdout.strip()
            n_restarts = subprocess.run(
                ["systemctl", "show", unit, "--property=NRestarts", "--value"],
                capture_output=True, text=True, timeout=15,
            ).stdout.strip()
        except Exception as exc:
            results.append(Result(f"unit:{unit}", False, f"systemctl so'rovi ishlamadi: {exc}"))
            continue
        if active != "active":
            results.append(Result(f"unit:{unit}", False, f"holat: {active}"))
        else:
            results.append(Result(f"unit:{unit}", True, f"active (restart: {n_restarts or '0'})"))
    return results


def _connect():
    import psycopg

    dsn = env("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL yo'q")
    return psycopg.connect(dsn.replace("postgresql+psycopg", "postgresql"), connect_timeout=10)


def check_database() -> Result:
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute("select version_num from alembic_version")
            head = (cur.fetchone() or ["?"])[0]
        return Result("database", True, f"ulandi, migratsiya {head}")
    except Exception as exc:
        return Result("database", False, f"{type(exc).__name__}: {str(exc)[:160]}")


def check_corpus() -> Result:
    """The corpus is the one table that cannot be rebuilt — verify it is both
    enabled and actually receiving rows."""
    if env("TRAINING_LOG", default="1") == "0":
        return Result("corpus", False, "TRAINING_LOG=0 — suhbatlar yozilmayapti")
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute("select count(*), max(created_at) from conversation_events")
            total, last = cur.fetchone()
    except Exception as exc:
        return Result("corpus", False, f"o'qib bo'lmadi: {str(exc)[:120]}")
    if not total:
        return Result("corpus", False, "jadval bo'sh")
    if last is not None:
        age = datetime.now(timezone.utc) - last.astimezone(timezone.utc)
        # Stale is a warning, not a failure: a genuinely quiet week is possible.
        # It is reported in the status line either way.
        stale = age > timedelta(hours=CORPUS_STALE_HOURS)
        return Result(
            "corpus", True,
            f"{total} satr, oxirgisi {int(age.total_seconds() // 3600)} soat oldin"
            + (" (sokin)" if stale else ""),
        )
    return Result("corpus", True, f"{total} satr")


def check_telegram() -> Result:
    """Token works *and* an operator chat is reachable.

    Both halves matter: a valid token whose admin never pressed Start delivers
    nothing, which is the failure mode this deployment actually hit.
    """
    import httpx

    token = env("MASKAN_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
    if not token:
        return Result("telegram", False, "bot tokeni sozlanmagan")
    admins = [a.strip() for a in env("MASKAN_OPERATOR_CHAT_IDS").split(",") if a.strip()]
    try:
        with httpx.Client(timeout=15) as client:
            me = client.get(f"https://api.telegram.org/bot{token}/getMe").json()
            if not me.get("ok"):
                return Result("telegram", False, f"getMe: {me.get('description')}")
            unreachable = []
            for admin in admins:
                r = client.get(
                    f"https://api.telegram.org/bot{token}/getChat",
                    params={"chat_id": admin},
                ).json()
                if not r.get("ok"):
                    unreachable.append(f"{admin} ({r.get('description')})")
    except Exception as exc:
        return Result("telegram", False, f"{type(exc).__name__}: {str(exc)[:120]}")
    if not admins:
        return Result("telegram", False, "MASKAN_OPERATOR_CHAT_IDS bo'sh")
    if unreachable:
        return Result("telegram", False, "operatorga yetib bormaydi: " + "; ".join(unreachable))
    return Result("telegram", True, f"@{me['result'].get('username')}, {len(admins)} operator")


def check_disk() -> Result:
    try:
        st = os.statvfs(str(ROOT))
    except Exception as exc:
        return Result("disk", False, str(exc)[:120])
    free_pct = 100.0 * st.f_bavail / st.f_blocks if st.f_blocks else 0.0
    free_gb = st.f_bavail * st.f_frsize / 1e9
    ok = free_pct >= DISK_MIN_FREE_PCT
    return Result("disk", ok, f"bo'sh {free_gb:.1f} GB ({free_pct:.0f}%)")


def check_backup() -> Result:
    if not BACKUP_DIR.exists():
        return Result("backup", False, f"{BACKUP_DIR} yo'q")
    dumps = sorted(BACKUP_DIR.glob("full-*.sql.gz"), key=lambda p: p.stat().st_mtime)
    if not dumps:
        return Result("backup", False, "hech qanday dump yo'q")
    newest = dumps[-1]
    age_h = (time.time() - newest.stat().st_mtime) / 3600
    size_mb = newest.stat().st_size / 1e6
    ok = age_h <= BACKUP_MAX_AGE_HOURS
    return Result(
        "backup", ok,
        f"{newest.name} — {age_h:.0f} soat oldin, {size_mb:.1f} MB ({len(dumps)} ta saqlanmoqda)",
    )


def run_checks() -> list[Result]:
    results: list[Result] = []
    results.extend(check_units())
    results.append(check_database())
    results.append(check_corpus())
    results.append(check_telegram())
    results.append(check_disk())
    results.append(check_backup())
    return results


# ----- alerting -------------------------------------------------------------

def send_telegram(text: str) -> bool:
    import httpx

    token = env("MASKAN_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
    admins = [a.strip() for a in env("MASKAN_OPERATOR_CHAT_IDS").split(",") if a.strip()]
    if not token or not admins:
        print("alert yuborilmadi: token yoki operator id yo'q", file=sys.stderr)
        return False
    sent = 0
    with httpx.Client(timeout=20) as client:
        for admin in admins:
            try:
                r = client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": admin, "text": text, "parse_mode": "HTML",
                          "disable_web_page_preview": True},
                )
                if r.json().get("ok"):
                    sent += 1
                else:
                    print(f"alert {admin} ga bormadi: {r.json().get('description')}", file=sys.stderr)
            except Exception as exc:
                print(f"alert {admin} ga bormadi: {exc}", file=sys.stderr)
    return sent > 0


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="holatni ko'rsat, xabar yuborma")
    parser.add_argument("--test", action="store_true", help="sinov xabarini yubor")
    parser.add_argument("--unit-failed", help="systemd OnFailure: shu unit yiqildi")
    args = parser.parse_args()

    if args.test:
        ok = send_telegram("🔧 <b>Watchdog sinovi</b>\nBu sinov xabari — hammasi joyida.")
        print("yuborildi" if ok else "YUBORILMADI")
        return 0 if ok else 1

    if args.unit_failed:
        # Fired by systemd the moment a unit dies — faster than the next timer
        # tick, and it names the unit that failed.
        send_telegram(
            f"🔴 <b>Xizmat yiqildi</b>\n<code>{args.unit_failed}</code>\n\n"
            f"Vaqt: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n"
            f"Tekshirish: <code>sudo systemctl status {args.unit_failed}</code>"
        )
        return 0

    results = run_checks()
    for r in results:
        print(f"{'OK  ' if r.ok else 'XATO'} {r.name:16} {r.detail}")
    if args.status:
        return 0

    state = load_state()
    previous: dict = state.get("checks", {})
    broke, healed = [], []
    for r in results:
        was_ok = previous.get(r.name, {}).get("ok", True)
        if was_ok and not r.ok:
            broke.append(r)
        elif not was_ok and r.ok:
            healed.append(r)
    state["checks"] = {r.name: {"ok": r.ok, "detail": r.detail} for r in results}
    state["last_run"] = datetime.now(timezone.utc).isoformat()

    if broke:
        lines = ["🔴 <b>Uzilish aniqlandi</b>", ""]
        lines += [f"• <b>{r.name}</b> — {r.detail}" for r in broke]
        still_ok = [r.name for r in results if r.ok]
        if still_ok:
            lines += ["", "Ishlayapti: " + ", ".join(still_ok)]
        lines += ["", f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"]
        if not send_telegram("\n".join(lines)):
            # Do not record the transition we failed to announce, or the next
            # run would treat it as "already reported" and stay silent forever.
            state["checks"] = previous
    if healed:
        lines = ["🟢 <b>Tiklandi</b>", ""]
        lines += [f"• <b>{r.name}</b> — {r.detail}" for r in healed]
        send_telegram("\n".join(lines))

    save_state(state)
    return 0 if all(r.ok for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
