from __future__ import annotations

from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

urlpatterns = [
    # The panel is nothing but the admin — send / straight to it.
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    path("admin/", admin.site.urls),
]
