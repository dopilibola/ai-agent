"""HTML formatting of oygul-specific operator notifications."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Optional

STATUS_PENDING = "pending"
STATUS_PAID = "paid"

STATUS_LABELS = {
    STATUS_PENDING: "⏳ Ожидание оплаты",
    STATUS_PAID: "✅ Оплачено",
}


@dataclass(frozen=True)
class CustomerInfo:
    chat_id: int
    name: str
    username: Optional[str]


@dataclass(frozen=True)
class OrderDetails:
    bouquet_name: str
    bouquet_photo_url: str
    bouquet_price_sum: int
    recipient_name: str
    recipient_phone: str
    address: str
    delivery_time: str
    delivery_fee_sum: int
    card_text: Optional[str] = None
    is_surprise: bool = False
    extra_notes: Optional[str] = None

    @property
    def total_sum(self) -> int:
        return self.bouquet_price_sum + self.delivery_fee_sum


def format_order_caption(
    customer: CustomerInfo, order: OrderDetails, status: str
) -> str:
    def esc(s: Optional[str]) -> str:
        return html.escape(s) if s else "—"

    def thousands(n: int) -> str:
        return f"{n:,}".replace(",", " ")

    status_label = STATUS_LABELS.get(status, status)
    lines = [
        "🌸 <b>Новый заказ</b>",
        f"<b>Статус:</b> {status_label}",
        "",
        f"<b>Букет:</b> {esc(order.bouquet_name)}",
        f"<b>Цена букета:</b> {thousands(order.bouquet_price_sum)} сум",
        f"<b>Доставка:</b> {thousands(order.delivery_fee_sum)} сум",
        f"<b>Итого:</b> {thousands(order.total_sum)} сум",
        "",
        f"<b>Получатель:</b> {esc(order.recipient_name)}",
        f"<b>Телефон:</b> {esc(order.recipient_phone)}",
        f"<b>Адрес:</b> {esc(order.address)}",
        f"<b>Время доставки:</b> {esc(order.delivery_time)}",
    ]
    if order.card_text:
        lines.append(f"<b>Открытка:</b> {html.escape(order.card_text)}")
    if order.is_surprise:
        lines.append("<b>Сюрприз:</b> да (курьер не звонит заранее)")
    if order.extra_notes:
        lines.append(f"<b>Примечание:</b> {html.escape(order.extra_notes)}")

    lines.append("")
    lines.append(f"<b>Клиент:</b> {html.escape(customer.name or '—')}")
    if customer.username:
        lines.append(f"<b>Username:</b> @{html.escape(customer.username)}")
    lines.append(f"<b>ID:</b> <code>{customer.chat_id}</code>")
    lines.append("")
    lines.append(
        f'<a href="tg://user?id={customer.chat_id}">Открыть чат с клиентом</a>'
    )
    return "\n".join(lines)


def format_status_reply(new_status: str, note: Optional[str]) -> str:
    label = STATUS_LABELS.get(new_status, new_status)
    lines = [f"🔔 <b>Статус обновлён:</b> {label}"]
    if note:
        lines.append("")
        lines.append(f"<i>{html.escape(note)}</i>")
    return "\n".join(lines)


def format_handoff_message(customer: CustomerInfo, reason: str) -> str:
    lines = [
        "🚨 <b>Lola просит помощь менеджера</b>",
        "",
        f"<b>Клиент:</b> {html.escape(customer.name or '—')}",
    ]
    if customer.username:
        lines.append(f"<b>Username:</b> @{html.escape(customer.username)}")
    lines.append(f"<b>ID:</b> <code>{customer.chat_id}</code>")
    lines.append("")
    lines.append(
        f'<a href="tg://user?id={customer.chat_id}">Открыть чат с клиентом</a>'
    )
    lines.append("")
    lines.append("<b>Причина:</b>")
    lines.append(f"<i>{html.escape(reason or '—')}</i>")
    return "\n".join(lines)
