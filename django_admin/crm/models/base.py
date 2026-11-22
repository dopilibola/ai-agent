"""Common base for every mirrored table.

`managed = False` is set once here so a new model cannot accidentally hand
Django ownership of a table Alembic maintains.
"""

from __future__ import annotations

from django.db import models


class CrmModel(models.Model):
    class Meta:
        abstract = True
        managed = False
