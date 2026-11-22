"""Admin registrations, split per tenant to mirror `crm/models/`.

Importing this package is what registers everything with the admin site;
Django's autodiscovery imports `crm.admin` on startup.
"""

from __future__ import annotations

from . import anfa, byd, maskan, oygul, shared  # noqa: F401
