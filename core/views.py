"""HTML pages. Each one is a server-rendered shell; its ES module in static/js/pages/ fills it from the JSON API.

No page receives secrets through the template context: everything a page shows comes from the
API, which decides per account what may be seen.
"""
from urllib.parse import quote

from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_safe

from .api import signed_in
from .models import Lease


def page(template, account=False, guests_only=False):
    """account=True: needs a signed-in account (else to the sign-in page, then back).
    guests_only=True: register / sign-in pages send a signed-in visitor on."""

    @require_safe
    @ensure_csrf_cookie  # the page's JS needs the cookie for its first POST
    def view(request, **kwargs):
        if account and not signed_in(request):
            return redirect(f"/login/?next={quote(request.get_full_path())}")
        if guests_only and signed_in(request):
            return redirect(safe_next(request))
        if "lease_id" in kwargs and not Lease.objects.filter(pk=kwargs["lease_id"]).exists():
            raise Http404
        # Only ids go into the page; who may see what is decided by the API.
        return render(request, template, {**{k: str(v) for k, v in kwargs.items()}, "next": safe_next(request)})

    return view


def safe_next(request):
    """Where to go after signing in: only ever a path on this site (no open redirect)."""
    target = request.GET.get("next") or ""
    if target.startswith("/") and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return "/leases/"


home = page("escrow/home.html")
register = page("escrow/register.html", guests_only=True)
login = page("escrow/login.html", guests_only=True)
account = page("escrow/account.html", account=True)
dashboard = page("escrow/dashboard.html", account=True)
lease_create = page("escrow/lease_create.html", account=True)
lease_detail = page("escrow/lease_detail.html", account=True)
arbiter = page("escrow/arbiter.html", account=True)
verify = page("escrow/verify.html")  # public on purpose: anyone can check a deposit
stage = page("escrow/stage.html")
terms = page("legal/terms.html")
privacy = page("legal/privacy.html")
