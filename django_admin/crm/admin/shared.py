"""Admin for the tenant-agnostic runtime tables.

Three of them — `muted_chats`, `chat_token_usage`, `chat_profiles` — are keyed
by ``(tenant_id, chat_id)``, and Django's admin refuses to register a model with
a composite primary key. Rather than lie about the key (using `chat_id` alone
would make two tenants' rows for the same Telegram user look like one object,
and an unmute would silently clear both), they are served by a purpose-built
page: the user directory at ``/admin/users/`` (`panel/views.py::users`), which
joins all three into one row per chat — who they are, which segment they fall
into, how many messages they sent and were sent — which is the view you
actually want anyway.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html

from crm.models import ConversationEvent, LearningExample
from panel.sites import thread_link, user_link

from .base import CrmAdmin, ReadOnlyAdmin, pretty_json


@admin.register(ConversationEvent)
class ConversationEventAdmin(ReadOnlyAdmin):
    """The corpus. Read-only on purpose — see `crm/admin/base.py`."""

    list_display = (
        "id",
        "created_at",
        "tenant_id",
        "role",
        "channel",
        "who",
        "short_text",
        "transcript",
    )
    list_filter = ("tenant_id", "role", "channel", "lang")
    search_fields = ("text", "thread_id", "chat_id", "turn_id")
    date_hierarchy = "created_at"
    readonly_fields = (
        "id",
        "created_at",
        "tenant_id",
        "chat_id",
        "thread_id",
        "channel",
        "agent",
        "turn_id",
        "role",
        "lang",
        "text",
        "meta_pretty",
        "transcript",
    )
    exclude = ("meta",)

    @admin.display(description="chat", ordering="chat_id")
    def who(self, obj):
        return user_link(obj.tenant_id, obj.chat_id)

    @admin.display(description="matn")
    def short_text(self, obj):
        text = (obj.text or "").strip().replace("\n", " ")
        return (text[:110] + "…") if len(text) > 110 else (text or "—")

    @admin.display(description="suhbat")
    def transcript(self, obj):
        return thread_link(obj.thread_id, "ochish")

    @admin.display(description="meta")
    def meta_pretty(self, obj):
        return pretty_json(obj.meta)


@admin.register(LearningExample)
class LearningExampleAdmin(CrmAdmin):
    """The review queue: `approved = NULL` means nobody has judged it yet.

    Approving here only flips the flag. The pgvector `embedding` is computed by
    `scripts/gold_review.py`; until it runs, an approved example is still found
    by the trigram fallback, so approving from the panel is safe but not the
    whole job.
    """

    list_display = (
        "id",
        "tenant_id",
        "verdict",
        "category",
        "stage",
        "short_question",
        "created_at",
        "reviewed_at",
    )
    list_filter = ("tenant_id", "approved", "category", "stage")
    search_fields = ("question", "human_reply", "ai_attempt", "source_thread_id")
    date_hierarchy = "created_at"
    readonly_fields = (
        "id",
        "tenant_id",
        "question",
        "ai_attempt",
        "human_reply",
        "source_thread_id",
        "source_event_id",
        "created_at",
        "context_pretty",
        "transcript",
    )
    fields = (
        "id",
        "tenant_id",
        "question",
        "ai_attempt",
        "human_reply",
        "approved",
        "category",
        "stage",
        "reviewed_at",
        "context_pretty",
        "source_thread_id",
        "source_event_id",
        "transcript",
        "created_at",
    )
    actions = ("approve", "reject")

    def has_add_permission(self, request):
        # Examples are harvested from real operator replies, not written by hand.
        return False

    @admin.display(description="holat", ordering="approved")
    def verdict(self, obj):
        # `format_html` requires at least one argument, so the label is passed
        # in rather than baked into the template string.
        if obj.approved is None:
            return format_html('<b style="color:#b26b00">{}</b>', "ko‘rilmagan")
        if obj.approved:
            return format_html('<span style="color:#1a7f37">{}</span>', "tasdiqlangan")
        return format_html('<span style="color:#a40e26">{}</span>', "rad etilgan")

    @admin.display(description="savol")
    def short_question(self, obj):
        text = (obj.question or "").strip().replace("\n", " ")
        return (text[:90] + "…") if len(text) > 90 else text

    @admin.display(description="kontekst")
    def context_pretty(self, obj):
        return pretty_json(obj.context)

    @admin.display(description="manba suhbat")
    def transcript(self, obj):
        return thread_link(obj.source_thread_id, "ochish")

    def _set(self, request, queryset, value: bool, label: str):
        from django.utils import timezone

        n = queryset.update(approved=value, reviewed_at=timezone.now())
        self.message_user(request, f"{n} ta misol {label}.", messages.SUCCESS)

    @admin.action(description="Tasdiqlash")
    def approve(self, request, queryset):
        self._set(request, queryset, True, "tasdiqlandi")

    @admin.action(description="Rad etish")
    def reject(self, request, queryset):
        self._set(request, queryset, False, "rad etildi")
