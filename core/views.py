"""HTML pages. Each one is a server-rendered shell; its ES module in static/js/pages/ fills it from the JSON API.

No page receives secrets through the template context. Pages for signed-in accounts redirect to the
home page (which has the Xaman sign-in) when nobody is signed in.
"""
from urllib.parse import quote

from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_safe

from .models import Invite, Lease


def _signed_in(request):
    return bool(request.session.get("party_id"))


def page(template, signed_in=False):
    @require_safe
    @ensure_csrf_cookie  # the page's JS needs the cookie for its first POST
    def view(request):
        if signed_in and not _signed_in(request):
            return redirect(f"/?next={quote(request.get_full_path())}")
        return render(request, template)

    return view


home = page("escrow/home.html")
dashboard = page("escrow/dashboard.html", signed_in=True)
lease_create = page("escrow/lease_create.html", signed_in=True)
terms = page("legal/terms.html")
privacy = page("legal/privacy.html")


@require_safe
@ensure_csrf_cookie
def lease_detail(request, lease_id):
    if not _signed_in(request):
        return redirect(f"/?next={quote(request.get_full_path())}")
    if not Lease.objects.filter(pk=lease_id).exists():
        raise Http404
    return render(request, "escrow/lease_detail.html", {"lease_id": str(lease_id)})


@require_safe
@ensure_csrf_cookie
def verify(request, lease_id):
    if not Lease.objects.filter(pk=lease_id).exclude(status=Lease.Status.PENDING_ACCEPTANCE).exists():
        raise Http404
    return render(request, "escrow/verify.html", {"lease_id": str(lease_id)})


@require_safe
@ensure_csrf_cookie
def invite(request, token):
    if not Invite.objects.filter(token=token, revoked=False).exists():
        raise Http404
    return render(request, "escrow/invite.html", {"token": token})
