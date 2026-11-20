"""HTML pages. Each one is a server-rendered shell; its ES module in static/js/pages/ fills it from the JSON API.

No page receives secrets through the template context: everything a page shows comes from the
API, which decides per persona what may be seen.
"""
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_safe

from .models import Lease


def page(template):
    @require_safe
    @ensure_csrf_cookie  # the page's JS needs the cookie for its first POST
    def view(request):
        return render(request, template)

    return view


home = page("escrow/home.html")
dashboard = page("escrow/dashboard.html")
lease_create = page("escrow/lease_create.html")
arbiter = page("escrow/arbiter.html")
demo = page("escrow/demo.html")
stage = page("escrow/stage.html")
terms = page("legal/terms.html")
privacy = page("legal/privacy.html")


def _lease_page(template):
    @require_safe
    @ensure_csrf_cookie
    def view(request, lease_id):
        if not Lease.objects.filter(pk=lease_id).exists():
            raise Http404
        # Only the id goes into the page; who may see what is decided by the API.
        return render(request, template, {"lease_id": str(lease_id)})

    return view


lease_detail = _lease_page("escrow/lease_detail.html")
verify = _lease_page("escrow/verify.html")
