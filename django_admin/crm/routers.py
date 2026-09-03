"""Route the `crm` models to the shared Postgres, and never migrate them.

Alembic (`db/migrations/`) owns the schema of every table the bots use. The
panel's models are `managed = False` mirrors of those tables, so
`allow_migrate` returns False for the whole app: `manage.py migrate` can only
ever create Django's own tables, and only in the SQLite `default` database.
"""

from __future__ import annotations

CRM_APP = "crm"
CRM_DB = "ai_sales"


class AiSalesRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label == CRM_APP:
            return CRM_DB
        return "default"

    def db_for_write(self, model, **hints):
        if model._meta.app_label == CRM_APP:
            return CRM_DB
        return "default"

    def allow_relation(self, obj1, obj2, **hints):
        # Relations are fine as long as both ends live in the same database.
        labels = {obj1._meta.app_label, obj2._meta.app_label}
        return len(labels - {CRM_APP}) == 0 or CRM_APP not in labels

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == CRM_APP:
            return False
        return db == "default"
