from __future__ import annotations

from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    # The panel is nothing but the admin — send / straight to it.
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    # `set_language`, which the topbar's uz/en switch posts to.
    path("i18n/", include("django.conf.urls.i18n")),
    path("admin/", admin.site.urls),
]
