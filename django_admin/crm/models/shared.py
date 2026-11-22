"""Shared, tenant-agnostic runtime state — mirrors of `db/models.py`.

Every model here is `managed = False`: Alembic owns these tables. Column sets
and types match `db/models.py` exactly; when you change one, change the other.
"""

from __future__ import annotations

from django.db import models

from .base import CrmModel


class MutedChat(CrmModel):
    """A chat where the agent is silenced because an operator took it over.

    Deleting a row here is exactly what the "Подключить ИИ" button does — the
    admin exposes that as an action, since un-muting from the panel is the one
    write to this table that is safe and useful.
    """

    pk = models.CompositePrimaryKey("tenant_id", "chat_id")
    tenant_id = models.CharField(max_length=64)
    chat_id = models.BigIntegerField()
    muted_at = models.DateTimeField()

    class Meta(CrmModel.Meta):
        db_table = "muted_chats"
        verbose_name = "muted chat"
        verbose_name_plural = "muted chats"
        ordering = ("-muted_at",)

    def __str__(self) -> str:
        return f"{self.tenant_id}:{self.chat_id}"


class ChatTokenUsage(CrmModel):
    """Per-chat token accounting. `current` = last run, `spent` = cumulative."""

    pk = models.CompositePrimaryKey("tenant_id", "chat_id")
    tenant_id = models.CharField(max_length=64)
    chat_id = models.BigIntegerField()

    current_input_tokens = models.IntegerField(default=0)
    current_cached_input_tokens = models.IntegerField(default=0)
    current_output_tokens = models.IntegerField(default=0)
    current_total_tokens = models.IntegerField(default=0)

    spent_input_tokens = models.BigIntegerField(default=0)
    spent_cached_input_tokens = models.BigIntegerField(default=0)
    spent_output_tokens = models.BigIntegerField(default=0)
    spent_total_tokens = models.BigIntegerField(default=0)

    updated_at = models.DateTimeField()

    class Meta(CrmModel.Meta):
        db_table = "chat_token_usage"
        verbose_name = "token usage"
        verbose_name_plural = "token usage"
        ordering = ("-spent_total_tokens",)

    def __str__(self) -> str:
        return f"{self.tenant_id}:{self.chat_id} — {self.spent_total_tokens}"


class ChatProfile(CrmModel):
    """Cached Telegram identity so a chat can be labelled by who it is."""

    pk = models.CompositePrimaryKey("tenant_id", "chat_id")
    tenant_id = models.CharField(max_length=64)
    chat_id = models.BigIntegerField()
    name = models.CharField(max_length=255, null=True, blank=True)
    username = models.CharField(max_length=255, null=True, blank=True)
    updated_at = models.DateTimeField()

    class Meta(CrmModel.Meta):
        db_table = "chat_profiles"
        verbose_name = "chat profile"
        verbose_name_plural = "chat profiles"
        ordering = ("-updated_at",)

    def __str__(self) -> str:
        label = self.name or self.username or str(self.chat_id)
        return f"{label} ({self.tenant_id})"


class ConversationEvent(CrmModel):
    """The append-only corpus: one row per event, not per turn.

    Read-only in the panel. This is the immutable record the agents are trained
    and debugged from — editing a row would corrupt exactly the thing it exists
    to preserve.
    """

    ROLE_CHOICES = [
        ("user", "user"),
        ("assistant", "assistant"),
        ("tool", "tool"),
        ("outbound", "outbound"),
        ("outcome", "outcome"),
        ("operator", "operator"),
    ]

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.CharField(max_length=64)
    chat_id = models.BigIntegerField()
    thread_id = models.CharField(max_length=255)
    channel = models.CharField(max_length=64, null=True, blank=True)
    agent = models.CharField(max_length=64, null=True, blank=True)
    turn_id = models.CharField(max_length=36, null=True, blank=True)
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    text = models.TextField(null=True, blank=True)
    lang = models.CharField(max_length=16, null=True, blank=True)
    meta = models.JSONField(default=dict)
    created_at = models.DateTimeField()

    class Meta(CrmModel.Meta):
        db_table = "conversation_events"
        verbose_name = "conversation event"
        verbose_name_plural = "conversation events"
        ordering = ("-id",)

    def __str__(self) -> str:
        return f"#{self.id} {self.role} {self.thread_id}"


class LearningExample(CrmModel):
    """A harvested operator answer awaiting (or carrying) a human verdict.

    `approved` is a nullable boolean on purpose — NULL means *pending review*,
    which is the whole queue `scripts/gold_review.py` fills and this panel is
    the natural place to work through.

    The table's `embedding` (pgvector) column is deliberately **not** mapped:
    Django has no native vector field, and leaving it out is safe because Django
    only ever writes the columns it knows about. `scripts/gold_review.py`
    recomputes it when an example is approved.
    """

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.CharField(max_length=64)
    question = models.TextField()
    context = models.JSONField(null=True, blank=True)
    ai_attempt = models.TextField(null=True, blank=True)
    human_reply = models.TextField()
    stage = models.CharField(max_length=32, null=True, blank=True)
    category = models.CharField(max_length=64, null=True, blank=True)
    approved = models.BooleanField(null=True, blank=True)
    source_thread_id = models.CharField(max_length=255, null=True, blank=True)
    source_event_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField()
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta(CrmModel.Meta):
        db_table = "learning_examples"
        verbose_name = "learning example"
        verbose_name_plural = "learning examples"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"#{self.id} {self.tenant_id}: {self.question[:60]}"


class PanelSetting(CrmModel):
    """Operator-editable knobs the bots read live — mirrors `db/models.py`.

    The panel is the only writer. A bot process reads the row through
    `db/settings.py` with a short TTL cache, so a switch flipped here reaches
    the running scheduler in seconds without a restart.

    Not registered as a model admin: a raw JSON blob is the wrong editing
    surface for "may the funnel write to clients at night". `panel/views.py`
    renders it as a form instead.
    """

    pk = models.CompositePrimaryKey("tenant_id", "key")
    tenant_id = models.CharField(max_length=64)
    key = models.CharField(max_length=64)
    value = models.JSONField(default=dict)
    updated_at = models.DateTimeField()

    class Meta(CrmModel.Meta):
        db_table = "panel_settings"
        verbose_name = "panel setting"
        verbose_name_plural = "panel settings"

    def __str__(self) -> str:
        return f"{self.tenant_id or '*'}:{self.key}"
