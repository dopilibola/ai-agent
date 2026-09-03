"""BYD Medical — detox clinic. Mirrors `apps/byd/models.py`.

The funnel is a faithful rebuild of the clinic's Bitrix24 pipeline, which is
why leads carry `bitrix_*` mirror ids: they are the link back to the deal in
Bitrix, written by `apps/byd/bitrix_sync.py`. Clearing one here would orphan
that link, so they are read-only in the admin.
"""

from __future__ import annotations

from django.db import models

from .base import CrmModel


class BydProgram(CrmModel):
    """The treatment programmes the agent quotes. `code` is the primary key."""

    code = models.CharField(max_length=16, primary_key=True)
    title = models.CharField(max_length=255)
    days = models.IntegerField(default=0)
    price = models.BigIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta(CrmModel.Meta):
        db_table = "byd_programs"
        verbose_name = "programme"
        verbose_name_plural = "programmes"
        ordering = ("code",)

    def __str__(self) -> str:
        return f"{self.code} — {self.title}"


class BydLead(CrmModel):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.CharField(max_length=64, default="byd")
    chat_id = models.BigIntegerField()
    current_stage = models.IntegerField(default=1)
    status = models.CharField(max_length=16, default="active")
    name = models.CharField(max_length=255, blank=True, default="")
    request = models.TextField(blank=True, default="")
    city = models.CharField(max_length=128, blank=True, default="")
    phone = models.CharField(max_length=64, blank=True, default="")
    tg_username = models.CharField(max_length=255, blank=True, default="")
    program = models.ForeignKey(
        BydProgram,
        on_delete=models.DO_NOTHING,
        db_column="program_code",
        db_constraint=False,
        to_field="code",
        null=True,
        blank=True,
        related_name="leads",
    )
    arrival_date = models.DateField(null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    total_amount = models.BigIntegerField(null=True, blank=True)
    prepayment_amount = models.BigIntegerField(null=True, blank=True)
    prepayment_received_at = models.DateTimeField(null=True, blank=True)
    voucher_number = models.IntegerField(null=True, blank=True)
    operator_first_action_at = models.DateTimeField(null=True, blank=True)
    last_call_outcome = models.CharField(max_length=32, null=True, blank=True)
    close_reason = models.CharField(max_length=255, null=True, blank=True)
    stage_entered_at = models.DateTimeField()
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    bitrix_contact_id = models.BigIntegerField(null=True, blank=True)
    bitrix_deal_id = models.BigIntegerField(null=True, blank=True)
    bitrix_stage_id = models.CharField(max_length=64, null=True, blank=True)

    class Meta(CrmModel.Meta):
        db_table = "byd_leads"
        verbose_name = "lead"
        verbose_name_plural = "leads (funnel)"
        ordering = ("-updated_at",)

    def __str__(self) -> str:
        return f"#{self.id} {self.name or self.chat_id} — stage {self.current_stage}"


class BydScheduledTask(CrmModel):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.CharField(max_length=64, default="byd")
    lead = models.ForeignKey(
        BydLead,
        on_delete=models.DO_NOTHING,
        db_column="lead_id",
        db_constraint=False,
        related_name="tasks",
    )
    chat_id = models.BigIntegerField()
    action_type = models.CharField(max_length=64)
    stage = models.IntegerField(default=0)
    payload = models.JSONField(default=dict)
    scheduled_for = models.DateTimeField()
    status = models.CharField(max_length=16, default="pending")
    attempts = models.IntegerField(default=0)
    dedup_key = models.CharField(max_length=255)
    last_error = models.TextField(null=True, blank=True)
    executed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta(CrmModel.Meta):
        db_table = "byd_scheduled_tasks"
        verbose_name = "scheduled task"
        verbose_name_plural = "scheduled tasks"
        ordering = ("scheduled_for",)

    def __str__(self) -> str:
        return f"{self.action_type} @ {self.scheduled_for:%Y-%m-%d %H:%M}"


class BydOperatorTask(CrmModel):
    """A to-do the funnel raised for a human operator (call, confirm, follow up)."""

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.CharField(max_length=64, default="byd")
    lead = models.ForeignKey(
        BydLead,
        on_delete=models.DO_NOTHING,
        db_column="lead_id",
        db_constraint=False,
        related_name="operator_tasks",
    )
    chat_id = models.BigIntegerField()
    kind = models.CharField(max_length=32)
    title = models.TextField(blank=True, default="")
    status = models.CharField(max_length=16, default="open")
    checklist = models.JSONField(default=list)
    due_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField()
    done_at = models.DateTimeField(null=True, blank=True)
    bitrix_task_id = models.BigIntegerField(null=True, blank=True)

    class Meta(CrmModel.Meta):
        db_table = "byd_operator_tasks"
        verbose_name = "operator task"
        verbose_name_plural = "operator tasks"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"#{self.id} {self.kind} — {self.status}"
