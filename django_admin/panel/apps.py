"""Swap in the panel's own AdminSite.

`django.contrib.admin.apps.AdminConfig` autodiscovers into `admin.site`; this
subclass points `default_site` at `panel.sites.PanelAdminSite` so the index page
can be a dashboard instead of a bare list of models, and so the site can carry
extra views (the conversation transcript) without monkey-patching.
"""

from __future__ import annotations

from django.contrib.admin.apps import AdminConfig


class PanelAdminConfig(AdminConfig):
    default_site = "panel.sites.PanelAdminSite"
