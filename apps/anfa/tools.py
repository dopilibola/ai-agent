"""Catalog tools — LangChain `@tool` wrappers around the KB + Postgres catalog.

The agent advises clients on the clinic's services and prices; there is no
online booking. `request_operator` flags the chat to clinic staff (a
notification with the client's contact) so a person can follow up — but it does
NOT mute the chat: anfa is an info bot and keeps answering questions even after
staff are notified.

The one exception is `handoff_for_results`: when the client wants to GET their
lab/analysis results (or a document a person must send), it BOTH notifies staff
and mutes the chat, so a human silently takes over — re-using oygul's
"Подключить ИИ" unmute button to hand the chat back to the bot afterwards.

Client identity (Telegram id/username) is read from the live channel via context
vars so the notification can deep-link back to the conversation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from langchain_core.tools import tool

from core.context import current_channel, current_chat_id
from notifications import unmute_button
from apps.anfa.kb_index import get_store
from apps.anfa.notifications import (
    build_handoff_message,
    build_results_handoff_message,
)
from apps.anfa.repository import get_repository
from apps.anfa.services import get_mute_store, get_notifier

logger = logging.getLogger(__name__)


@tool
async def search_services(query_text: str, n_results: int = 8) -> str:
    """Search the clinic's service catalog by natural language and get prices.
    Use whenever the client asks about a service, a speciality, a lab test, a
    procedure, or how much something costs.

    Returns matching services, each with its title, category/speciality, and
    price in UZS sum. Translate the client's complaint into the matching
    service or speciality before searching (e.g. a stomach complaint → search
    "гастроэнтеролог" / "gastroenterolog"). Prices come straight from the
    catalog — quote them as-is, never invent or round a price that wasn't
    returned."""
    store = get_store()
    coll = store.collection
    # The collection also holds doctor cards; a metadata `where` filter keeps
    # the query to service docs only, so a crowded neighbourhood (e.g. lots of
    # gynaecology services) can't starve the result.
    result = await asyncio.to_thread(
        coll.query,
        query_texts=[query_text],
        n_results=max(1, n_results),
        where={"type": "service"},
    )
    metadatas = (result.get("metadatas") or [[]])[0]
    services: list[dict] = []
    for meta in metadatas:
        if not meta:
            continue
        services.append(
            {
                "title": meta.get("title"),
                "category": meta.get("category"),
                "tab": meta.get("tab"),
                "price": meta.get("price") or 0,
                "currency": meta.get("currency") or "UZS",
            }
        )
    return json.dumps({"services": services}, ensure_ascii=False)


@tool
async def search_doctors(query_text: str, n_results: int = 5) -> str:
    """Find doctors by speciality or name and get their walk-in hours. Use when
    the client asks who the clinic's doctor for something is, asks for a named
    doctor, or wants to know when a specialist receives patients.

    Returns matching doctors with `fullname`, `speciality`, `experience`, and
    `hours_label` — the reception hours, e.g. "Mon–Sat 09:00–14:00". Present the
    hours as when to COME IN (walk-in); there is no online booking, so tell the
    client to visit during those hours (translate the weekday names into the
    client's language). Map the client's complaint to a speciality before
    searching, same as for services."""
    store = get_store()
    coll = store.collection
    # Filter to doctor docs only (the collection is mostly service rows) via a
    # metadata `where`, so the doctor cards are never crowded out of the top-N.
    result = await asyncio.to_thread(
        coll.query,
        query_texts=[query_text],
        n_results=max(1, n_results),
        where={"type": "doctor"},
    )
    metadatas = (result.get("metadatas") or [[]])[0]
    doctors: list[dict] = []
    for meta in metadatas:
        if not meta:
            continue
        doctors.append(
            {
                "fullname": meta.get("fullname"),
                "speciality": meta.get("speciality"),
                "experience": meta.get("experience"),
                "hours_label": meta.get("hours_label"),
            }
        )
    return json.dumps({"doctors": doctors}, ensure_ascii=False)


@tool
async def list_service_categories() -> str:
    """List the catalog's groups (tab + speciality/category) with how many
    services each has. Use to give the client an overview of what the clinic
    offers, or to help them pick a direction when their request is vague."""
    cats = await get_repository().list_categories()
    return json.dumps({"categories": cats}, ensure_ascii=False)


async def _client_identity() -> Optional[dict]:
    """Pull the client's Telegram identity from the live channel for the
    operator deep-link."""
    channel = current_channel.get()
    chat_id = current_chat_id.get()
    if channel is None or chat_id is None:
        return None
    info = await channel.get_chat_info(chat_id)
    return {
        "tg_id": chat_id,
        "tg_username": info.get("username") or "",
        "tg_first_name": info.get("name") or "",
    }


@tool
async def request_operator(reason: str, summary: str = "") -> str:
    """Notify clinic staff that a client wants a human to follow up (e.g. call
    them back). Call this when the client asks to talk to a person, wants to
    register/confirm a visit, or asks something you cannot answer from the
    catalog.

    reason  : a short reason (e.g. "хочет записаться", "savol operatorga").
    summary : optional one-line summary of what the client is interested in
              (the services/prices discussed) so staff have context.

    This only NOTIFIES staff — it does NOT pause you. Keep answering the
    client's questions as usual; just let them know a staff member will reach
    out (and that they can also call the call center)."""
    patient = await _client_identity()
    message = build_handoff_message(reason=reason, summary=summary, patient=patient)
    await get_notifier().notify_text(message)
    return json.dumps({"success": True, "notified": True}, ensure_ascii=False)


@tool
async def handoff_for_results(
    summary: str = "", client_name: str = "", client_birthdate: str = ""
) -> str:
    """Hand the chat to a human — FOR LAB / ANALYSIS RESULTS ONLY.

    Call this ONLY when the client wants to GET their analysis results, lab
    answers, or a receipt/document that a person has to send them
    ("анализ жавоблари", "результаты анализов", "натижаларни олсам bo'ladimi",
    "чек / квитанция"). This does TWO things at once: it notifies clinic staff
    (with a button to re-enable the bot) AND pauses the bot for this chat, so a
    human silently takes over and sends the results.

    Do NOT use this for anything else. General "I want to talk to a person",
    registering/confirming a visit, or a price you can't find still go through
    `request_operator` (which keeps the bot answering). This handoff is
    exclusively for delivering results/documents.

    Before calling, make sure you have the client's full name (name AND
    surname) and date of birth (day, month, year) — staff need them to find the
    right results. The Telegram profile does not carry them, so if either is
    missing from the conversation, politely ask the client for it first and only
    call this once you have it (or the client declined to share).

    summary          : optional one-line note of what the client is asking for.
    client_name      : the client's full name (name + surname) as they stated it.
    client_birthdate : the client's date of birth (day, month, year) as stated.

    After it returns, send ONE short reply in the client's language: that
    you're passing this to a colleague who will send the results, and to
    please wait — calm and unhurried, no "one second / any minute now"
    phrasing. Do NOT tell them to call the clinic; a person is already
    handling it. Then STOP — do not keep chatting; a person now owns the chat."""
    patient = await _client_identity()
    chat_id = current_chat_id.get()
    message = build_results_handoff_message(
        summary=summary,
        patient=patient,
        client_name=client_name,
        client_birthdate=client_birthdate,
    )
    markup = unmute_button(chat_id) if chat_id is not None else None
    await get_notifier().notify_text(message, reply_markup=markup)
    # Mute AFTER notifying so staff are reached even if the store write fails.
    # The mute is checked at the START of dispatch, so this same turn's reply
    # ("hozir bir daqiqa…") is still delivered; only later messages go silent.
    if chat_id is not None:
        try:
            await get_mute_store().mute(chat_id)
        except Exception:
            logger.exception("Failed to mute chat %s for results handoff", chat_id)
    return json.dumps({"success": True, "muted": True}, ensure_ascii=False)


CATALOG_TOOLS = [
    search_services,
    search_doctors,
    list_service_categories,
    request_operator,
    handoff_for_results,
]
