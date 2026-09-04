"""Is it running, and is it working — the two questions the panel could not answer.

The panel is where someone looks when something feels wrong, and until now the
only way to find out whether the bot was alive was to SSH in. This reads the
same facts the watchdog does (`scripts/watchdog.py`), but on demand and in a
browser: the systemd units, whether the corpus is still being written, and how
long ago a real customer was answered.

Two deliberate limits:

* **The unit list is a constant**, and only the bot units may be restarted. The
  panel's own unit is listed but not restartable — restarting the process that
  is rendering the page cannot report its own result.
* **`systemctl` is invoked as an argument list, never through a shell**, so a
  unit name can never become a command. Reading state needs no privileges;
  restarting uses `sudo -n`, which fails cleanly if the deploy has not granted
  it rather than hanging on a password prompt.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

# The units this panel knows about. `restartable` is False for the panel itself.
UNITS: tuple[tuple[str, str, bool], ...] = (
    ("ai-agent-maskan", "Maskan bot (userbot + operator + rejalashtiruvchi)", True),
    ("ai-agent-maskan-payments", "To‘lov qabul qiluvchi (Payme/Uzum)", True),
    ("ai-agent-panel", "Shu panelning o‘zi", False),
)
RESTARTABLE = {name for name, _label, ok in UNITS if ok}

_SHOW_FIELDS = ("ActiveState", "SubState", "ActiveEnterTimestamp", "NRestarts", "UnitFileState")
_TIMEOUT = 8


@dataclass
class UnitStatus:
    name: str
    label: str
    restartable: bool
    active_state: str = "unknown"
    sub_state: str = ""
    since: str = ""
    restarts: str = "0"
    enabled: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.active_state == "active"

    @property
    def installed(self) -> bool:
        """A unit that was never installed on this host is not a failure."""
        return self.enabled not in ("", "not-found")

    @property
    def switched_off(self) -> bool:
        """Installed, disabled and not running: somebody turned it off on
        purpose. That is a state to report, not an incident to alarm about —
        an alert people learn to dismiss is worse than no alert."""
        return self.installed and not self.ok and self.enabled == "disabled"


def _systemctl(args: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["systemctl", *args],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
        return proc.returncode, (proc.stdout or proc.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def unit_statuses() -> list[UnitStatus]:
    out: list[UnitStatus] = []
    for name, label, restartable in UNITS:
        status = UnitStatus(name=name, label=label, restartable=restartable)
        code, text = _systemctl(
            ["show", name, "--property=" + ",".join(_SHOW_FIELDS)]
        )
        if code != 0 and not text:
            status.error = "systemctl javob bermadi"
            out.append(status)
            continue
        values = dict(
            line.split("=", 1) for line in text.splitlines() if "=" in line
        )
        status.active_state = values.get("ActiveState", "unknown")
        status.sub_state = values.get("SubState", "")
        status.since = values.get("ActiveEnterTimestamp", "")
        status.restarts = values.get("NRestarts", "0")
        status.enabled = values.get("UnitFileState", "")
        out.append(status)
    return out


def restart_unit(name: str) -> tuple[bool, str]:
    """Restart one allow-listed unit. Returns (ok, message)."""
    if name not in RESTARTABLE:
        return False, "Bu xizmatni paneldan qayta ishga tushirib bo‘lmaydi."
    try:
        proc = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", name],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Bajarib bo‘lmadi: {exc}"
    if proc.returncode == 0:
        return True, f"{name} qayta ishga tushirildi."
    detail = (proc.stderr or proc.stdout or "").strip()[:300]
    if "password" in detail.lower() or "sudo:" in detail.lower():
        detail = (
            "serverda ruxsat yo‘q — panel foydalanuvchisiga parolsiz "
            "`systemctl restart` ruxsati berilishi kerak"
        )
    return False, f"Bajarilmadi: {detail or 'sabab noma’lum'}"


def activity() -> dict:
    """What the database says about whether the bot is actually working.

    A unit can be `active` and still be useless — an unauthorised Telegram
    session, a wedged poller. These are the signs of real work: a customer
    message arriving, an answer going back, the corpus still being appended to.
    """
    from crm.analytics import EVAL_CHAT_MAX, EVAL_CHAT_MIN
    from crm.models import ConversationEvent, MaskanScheduledTask

    now = timezone.now()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    data: dict = {"db_ok": True, "error": ""}
    try:
        last = {
            role: ConversationEvent.objects.filter(role=role)
            .order_by("-id")
            .values_list("created_at", flat=True)
            .first()
            for role in ("user", "assistant", "outbound", "operator")
        }
        data["last"] = last
        data["events_24h"] = ConversationEvent.objects.filter(
            created_at__gte=day_ago
        ).count()
        data["events_7d"] = ConversationEvent.objects.filter(
            created_at__gte=week_ago
        ).count()
        # The eval runner reserves a high chat-id block and does not always
        # clean up after a crash. Its leftovers are exactly the kind of thing
        # that makes a health page cry wolf, so they are excluded here for the
        # same reason every other page excludes them.
        tasks = MaskanScheduledTask.objects.exclude(
            chat_id__gte=EVAL_CHAT_MIN, chat_id__lte=EVAL_CHAT_MAX
        )
        data["tasks_pending"] = tasks.filter(status="pending").count()
        data["tasks_failed"] = tasks.filter(status="failed").count()
        data["tasks_running"] = tasks.filter(status="running").count()
        data["tasks_failed_eval"] = MaskanScheduledTask.objects.filter(
            status="failed", chat_id__gte=EVAL_CHAT_MIN, chat_id__lte=EVAL_CHAT_MAX
        ).count()
    except Exception as exc:  # the database being unreachable *is* the answer
        data["db_ok"] = False
        data["error"] = str(exc)[:300]
    return data


def summary() -> dict:
    """Everything the health page shows, plus one overall verdict."""
    units = unit_statuses()
    act = activity()
    problems: list[str] = []
    notes: list[str] = []
    for u in units:
        if u.switched_off:
            notes.append(f"{u.label} — qo‘lda o‘chirib qo‘yilgan.")
        elif u.installed and not u.ok:
            problems.append(f"{u.label} ishlamayapti ({u.active_state}).")
    if not act.get("db_ok"):
        problems.append("Ma’lumotlar bazasiga ulanib bo‘lmadi.")
    else:
        # "Nobody wrote today" is not a fault — it is a quiet day, and calling
        # it a fault teaches people to ignore this page. The real fault is a
        # customer message with no answer after it.
        last = act.get("last") or {}
        user_at, reply_at = last.get("user"), last.get("assistant")
        if user_at and (reply_at is None or reply_at < user_at):
            waited = timezone.now() - user_at
            if waited > timedelta(minutes=15):
                problems.append(
                    "Mijoz yozgan, javob berilmagan — "
                    f"{int(waited.total_seconds() // 60)} daqiqa bo‘ldi."
                )
        if not act.get("events_24h"):
            notes.append("Oxirgi sutkada hech kim yozmadi — tinch kun.")
    if act.get("tasks_failed"):
        problems.append(f"{act['tasks_failed']} ta rejadagi xabar xato bilan tugagan.")
    if act.get("tasks_failed_eval"):
        notes.append(
            f"{act['tasks_failed_eval']} ta xato test (eval) chatiga tegishli — "
            "hisobga olinmadi."
        )
    return {"units": units, "activity": act, "problems": problems, "notes": notes}
