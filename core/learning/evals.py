"""Eval harness — the scoreboard a prompt change has to beat.

A case is a short scripted conversation plus assertions about what the agent
should have *done*, not about the exact words it used. Wording drifts between
models and prompt edits; behaviour is what must not regress:

  * the tools it called (and the ones it must not have called),
  * whether every price it said aloud exists in the catalogue,
  * whether it opened an order without a clear yes,
  * the script/language it answered in,
  * substrings that must (or must not) appear — a cemetery's catalogue spelling,
    a refusal, a link.

Deliberately no LLM judge in this layer. These checks are exact, free and never
flaky, which is what makes them safe to block a deploy on. A judged layer can
sit on top later; it should not sit underneath.

Cases live with the tenant (`apps/<tenant>/evals/cases.jsonl`) because they
encode that business's rules. The runner is here because the mechanics are not
tenant-specific.

The tool trace is read back from `conversation_events` rather than scraped out
of the graph: it is already written for every turn, already carries the `ok`
flag, and using it means the evals exercise the same logging path production
depends on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

# Every real price in this catalogue is six figures, while the numbers that
# legitimately appear in a reply — years of birth and death, visit counts —
# are four at most. A floor of 10 000 separates them without a parser, and
# years are additionally excluded by range so a future cheap service cannot
# re-introduce the confusion.
MONEY_FLOOR = 10_000
YEAR_RANGE = range(1800, 2200)
# Cyrillic block, minus the Latin letters that appear inside brand names.
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_LATIN_WORD = re.compile(r"\b[A-Za-z]{4,}\b")


@dataclass
class Case:
    id: str
    turns: list[str]
    expect: dict
    note: str = ""

    @staticmethod
    def load(path: Path) -> list["Case"]:
        cases: list[Case] = []
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: {exc}") from exc
            cases.append(Case(
                id=raw["id"], turns=raw["turns"],
                expect=raw.get("expect", {}), note=raw.get("note", ""),
            ))
        return cases


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)


def _numbers(text: str) -> list[int]:
    """Money-looking figures in a reply.

    Uzbek and Russian both group thousands with spaces (and the model sometimes
    uses none), so digits separated by spaces/NBSP are joined before parsing.
    """
    joined = re.sub(r"(?<=\d)[    ](?=\d{3}\b)", "", text)
    return [
        n for n in (int(m) for m in re.findall(r"\b\d{4,}\b", joined))
        if n not in YEAR_RANGE
    ]


def _is_cyrillic(text: str) -> bool:
    letters = _CYRILLIC.findall(text)
    latin = _LATIN_WORD.findall(text)
    return len(letters) > 3 * len(latin)


def check(
    case: Case,
    *,
    replies: list[str],
    tools: list[str],
    failed_tools: list[str],
    allowed_amounts: set[int],
) -> CaseResult:
    """Run every assertion the case declares. Pure — no I/O, so it is testable
    on recorded transcripts too."""
    expect = case.expect
    failures: list[str] = []
    last = replies[-1] if replies else ""
    joined = "\n".join(replies)

    for name in expect.get("tools_called", []):
        if name not in tools:
            failures.append(f"`{name}` chaqirilmadi (chaqirilganlari: {tools or '—'})")
    for name in expect.get("tools_not_called", []):
        if name in tools:
            failures.append(f"`{name}` chaqirilmasligi kerak edi")

    # `reply_contains` spans the whole conversation — right for "did it ever
    # say this". `last_reply_contains` looks only at the final message, which is
    # what a rule about *this* answer needs: a price mentioned three turns ago
    # is not a price the customer is choosing from now.
    for needle in expect.get("reply_contains", []):
        if needle.lower() not in joined.lower():
            failures.append(f"suhbatda «{needle}» umuman aytilmadi")
    for needle in expect.get("reply_excludes", []):
        if needle.lower() in joined.lower():
            failures.append(f"suhbatda «{needle}» bo'lmasligi kerak edi")
    # Proper names legitimately appear in either script: the prompt tells the
    # agent to answer in Cyrillic but to copy catalogue names verbatim, so
    # "Dombirobod" and "Домбиробод" are both correct. `*_any` passes when one
    # of the spellings shows up, instead of forcing a spelling the rule does
    # not actually require.
    for group in expect.get("reply_contains_any", []):
        if not any(n.lower() in joined.lower() for n in group):
            failures.append("suhbatda bularning hech biri yo'q: " + " / ".join(group))
    for group in expect.get("last_reply_contains_any", []):
        if not any(n.lower() in last.lower() for n in group):
            failures.append("oxirgi javobda bularning hech biri yo'q: " + " / ".join(group))
    for needle in expect.get("last_reply_contains", []):
        if needle.lower() not in last.lower():
            failures.append(f"oxirgi javobda «{needle}» yo'q")
    for needle in expect.get("last_reply_excludes", []):
        if needle.lower() in last.lower():
            failures.append(f"oxirgi javobda «{needle}» bo'lmasligi kerak edi")

    if expect.get("script") == "cyrillic" and last and not _is_cyrillic(last):
        failures.append("javob kirillda emas")

    if expect.get("prices_from_catalog", True):
        for value in _numbers(joined):
            if value >= MONEY_FLOOR and value not in allowed_amounts:
                failures.append(
                    f"katalogda yo'q summa aytildi: {value:,}".replace(",", " ")
                )
                break

    if expect.get("ends_with_question") and last and "?" not in last[-220:]:
        failures.append("javob savol bilan tugamadi (keyingi qadam noaniq)")

    if failed_tools and expect.get("allow_tool_errors", False) is False:
        failures.append(f"tool xatosi: {', '.join(sorted(set(failed_tools)))}")

    return CaseResult(case.id, not failures, failures, replies, tools)


async def collect_trace(conn, thread_id: str) -> tuple[list[str], list[str], list[str]]:
    """(replies, tool names, failed tool names) for one eval thread."""
    cur = conn.cursor()
    cur.execute(
        """
        select role, text, meta->>'tool', meta->>'ok'
        from conversation_events where thread_id = %s order by id
        """,
        (thread_id,),
    )
    replies, tools, failed = [], [], []
    for role, text, tool, ok in cur.fetchall():
        if role == "assistant":
            replies.append(text or "")
        elif role == "tool" and tool:
            tools.append(tool)
            if ok == "false":
                failed.append(tool)
    return replies, tools, failed


def render(results: list[CaseResult]) -> str:
    lines = []
    passed = [r for r in results if r.passed]
    for r in results:
        mark = "\033[32m✓\033[0m" if r.passed else "\033[31m✗\033[0m"
        lines.append(f"{mark} {r.case_id}")
        for failure in r.failures:
            lines.append(f"    · {failure}")
    lines.append("")
    lines.append(f"{len(passed)}/{len(results)} o'tdi")
    return "\n".join(lines)
