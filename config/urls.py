"""URL configuration. Patch 1 exposes only the Django admin; pages and the API come in later patches."""
from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
