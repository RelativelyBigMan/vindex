"""URL configuration: the Django admin, the HTML pages and the JSON API."""
from django.contrib import admin
from django.templatetags.static import static
from django.urls import include, path
from django.views.generic import RedirectView

from core import views
from core.urls import api_patterns, escrow_patterns, legal_patterns

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", views.home, name="home"),
    path("favicon.ico", RedirectView.as_view(url=static("img/favicon.svg"), permanent=False)),
    path("", include((escrow_patterns, "escrow"))),
    path("legal/", include((legal_patterns, "legal"))),
    path("", include(api_patterns)),
]
