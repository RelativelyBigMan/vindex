"""URL configuration: the Django admin and the JSON API. HTML pages come in Patch 4."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
]
