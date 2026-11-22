"""How close is this person to buying — the number the panel is organised around.

An operator opening the panel has one question: **who do I talk to today?** A
list sorted by "last seen" does not answer it; a list sorted by a probability
does. So every chat gets a percentage here, and the categories on the users page
are buckets of that percentage rather than a separate taxonomy.

Three properties matter more than accuracy:

* **Explainable.** The score is a sum of named signals, and every page that
  shows a percentage can show the exact lines that produced it. An operator who
  cannot see why a lead is 70% will not trust the 70%, and a number nobody
  trusts is worse than no number.
* **Derived, never stored.** Nothing here is written to the database. Changing a
  weight re-scores all of history on the next page load — which is what makes it
  safe to tune.
* **Behaviour, not words.** Every signal is something that happened: how far the
  funnel got, whether they answered our last message, whether they left a phone
  number, how long ago they wrote. No sentiment, no model call.

The funnel stage carries most of the weight, because it is the only signal that
is a *commitment*: someone who asked for a price for a named grave is a
different person from someone who said hello. Recency then multiplies the whole
thing down — an 80% lead who went quiet three weeks ago is not an 80% lead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Bucket:
    """One category on the users page: a slice of the probability scale."""

    key: str
    label: str
    hint: str
    color: str
    floor: int = 0  # lowest percent that lands here (terminal buckets: -1)


# Ordered hottest first — the order the legend and the filter show them in.
BUCKETS: tuple[Bucket, ...] = (
    Bucket("mijoz", "Sotib olgan", "To‘lovi o‘tgan — endi xizmat ko‘rsatiladi", "#12b76a", -1),
    Bucket("issiq", "Juda yaqin", "Narx aytilgan, javob beryapti — bugun gaplashing", "#f04438", 65),
    Bucket("iliq", "Yaqin", "Qiziqyapti, hali qaror qilmagan", "#f79009", 40),
    Bucket("sovuq", "O‘ylayapti", "Boshlagan, lekin sust", "#2563eb", 15),
    Bucket("muz", "Uzoq", "Deyarli yo‘q — o‘zi yozsa javob beramiz", "#98a2b1", 0),
    Bucket("yopilgan", "Yopilgan", "Rad etgan yoki «yozmang» degan", "#667085", -1),
)
BUCKET_BY_KEY = {b.key: b for b in BUCKETS}

# Terminal buckets are decided by facts, not by the scale.
PAID = BUCKET_BY_KEY["mijoz"]
LOST = BUCKET_BY_KEY["yopilgan"]

# How much of the score survives a silence of this many days. A lead is not
# rejected by going quiet — it is just no longer the one to call first.
_DECAY = ((1, 1.00), (3, 0.93), (7, 0.82), (14, 0.62), (30, 0.38))
_DECAY_FLOOR = 0.18

# The commitment ladder. Keys are `maskan_leads.current_stage`.
_STAGE_BASE = {
    0: 8,    # no lead row at all — they wrote, nothing more
    1: 12,   # lead opened, we don't know whose grave yet
    2: 28,   # grave and cemetery known
    3: 48,   # a concrete price for concrete services was given
    4: 66,   # order opened, payment link in their hands
}


@dataclass
class Score:
    percent: int
    bucket: Bucket
    reasons: list[tuple[str, int]] = field(default_factory=list)

    @property
    def bar_width(self) -> int:
        """Never render a 0-width bar for a live lead — 2% still shows a sliver."""
        return max(2, self.percent) if self.percent else 0


def _days_since(when: datetime | None, now: datetime) -> float | None:
    if when is None:
        return None
    return max(0.0, (now - when).total_seconds() / 86400.0)


def _decay(days: float | None) -> float:
    if days is None:
        return _DECAY_FLOOR
    for limit, factor in _DECAY:
        if days <= limit:
            return factor
    return _DECAY_FLOOR


def _bucket_for(percent: int) -> Bucket:
    for bucket in BUCKETS:
        if bucket.floor >= 0 and percent >= bucket.floor:
            return bucket
    return BUCKET_BY_KEY["muz"]


def score_row(row, now: datetime) -> Score:
    """Probability that this chat buys, plus the lines that explain it.

    `row` is an `analytics.ChatRow`; it is typed loosely on purpose so the two
    modules do not import each other in a circle.
    """
    # ----- terminal facts beat every signal --------------------------------
    #
    # `won` is written only by the panel's «Sotib olgan» button — an operator
    # asserting a sale the system did not see (cash, a transfer, a deal closed
    # by phone). It moves the category, never the revenue: money still comes
    # from real paid orders, so a hand-marked customer cannot inflate a KPI.
    if row.paid_orders:
        return Score(100, PAID, [("To‘langan buyurtmasi bor", 100)])
    if row.lead_status == "won":
        return Score(100, PAID, [("Operator «sotib olgan» deb belgilagan", 100)])
    if row.lead_status == "closed":
        why = row.close_reason or "yopilgan"
        return Score(0, LOST, [(f"Yopilgan: {why}", 0)])

    reasons: list[tuple[str, int]] = []

    stage = row.lead_stage or 0
    if stage >= 5:
        return Score(100, PAID, [("To‘lov o‘tgan (5-bosqich)", 100)])
    base = _STAGE_BASE.get(stage, _STAGE_BASE[0])
    reasons.append((_stage_label(stage), base))
    total = float(base)

    # ----- behaviour on top of the stage -----------------------------------
    if row.inbound:
        pts = min(row.inbound, 7) * 2
        total += pts
        reasons.append((f"{row.inbound} ta xabar yozgan", pts))

    if row.orders and not row.paid_orders:
        total += 10
        reasons.append(("Buyurtma ochilgan, to‘lov kutilmoqda", 10))

    if row.phone:
        total += 6
        reasons.append(("Telefon raqamini bergan", 6))

    if row.answered_last:
        total += 8
        reasons.append(("Oxirgi xabarimizga javob bergan", 8))
    elif row.touches and row.last_outbound_at and not row.answered_last:
        total -= 6
        reasons.append(("Yozdik — javob bermadi", -6))

    # ----- and how long ago all of it happened ------------------------------
    days = _days_since(row.last_inbound_at or row.last_seen, now)
    factor = _decay(days)
    if factor < 1.0:
        before = total
        total *= factor
        reasons.append((_silence_label(days), int(round(total - before))))

    if row.lead_status == "cold":
        before = total
        total *= 0.7
        reasons.append(("Kuzatuv to‘xtatilgan (sovigan)", int(round(total - before))))

    percent = max(1, min(97, int(round(total))))
    return Score(percent, _bucket_for(percent), reasons)


def _stage_label(stage: int) -> str:
    return {
        0: "Yozgan, murojaat ochilmagan",
        1: "1-bosqich: tanishuv",
        2: "2-bosqich: qabr ma’lum",
        3: "3-bosqich: narx aytilgan",
        4: "4-bosqich: to‘lov havolasi berilgan",
    }.get(stage, f"{stage}-bosqich")


def _silence_label(days: float | None) -> str:
    if days is None:
        return "Faolligi noma’lum"
    if days < 1:
        return "Bugun yozgan"
    return f"{int(days)} kundan beri jim"
