"""JSON API (the contract the frontend is built against).

Errors are always {"error": friendly message, "code": engine result or internal code} with
HTTP 400 (bad input, ledger refusal), 401 (not signed in), 409 (not allowed in this state / for
this account) or 500 (our fault, or the ledger can't be reached). Every POST needs Django's CSRF
token in X-CSRFToken.

The fulfillment and every seed stay on the server.
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime, time, timedelta
from functools import wraps

from django.core.cache import cache
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from ledger import services, xaman
from ledger.client import explorer_account, explorer_tx, network, network_key
from ledger.errors import LedgerError
from xrpl.core.addresscodec import is_valid_classic_address

from . import agreements, faucet, lifecycle, signing
from .lifecycle import InvalidInput, InvalidTransition, plain
from .models import Currency, Evidence, Invite, Lease, Party, SettlementProposal, XamanRequest

log = logging.getLogger(__name__)

MAX_EVIDENCE_BYTES = 5 * 1024 * 1024
EVIDENCE_TYPES = {  # extension -> file signature check
    ".jpg": lambda head: head[:3] == b"\xff\xd8\xff",
    ".jpeg": lambda head: head[:3] == b"\xff\xd8\xff",
    ".png": lambda head: head[:8] == b"\x89PNG\r\n\x1a\n",
    ".webp": lambda head: head[:4] == b"RIFF" and head[8:12] == b"WEBP",
}
SERVER_SIDE_CODES = {"network_error", "unknown", "xaman_error", "xaman_not_configured"}
EVIDENCE_CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


# ---- plumbing ----

class NotSignedIn(Exception):
    message, code = "Sign in with Xaman first.", "signed_out"


def _error(message, code, status):
    return JsonResponse({"error": message, "code": code}, status=status)


def api(*methods):
    """Method check, JSON in / JSON out, and one place that turns exceptions into contract errors."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                return _error("This endpoint doesn't support that method.", "method_not_allowed", 405)
            try:
                if request.method == "GET":
                    get_token(request)  # make sure the CSRF cookie exists before the first POST
                result = view(request, *args, **kwargs)
            except InvalidInput as exc:
                return _error(exc.message, exc.code, 400)
            except NotSignedIn as exc:
                return _error(exc.message, exc.code, 401)
            except InvalidTransition as exc:
                return _error(exc.message, exc.code, 409)
            except LedgerError as exc:
                return _error(exc.message, exc.engine_result, 500 if exc.engine_result in SERVER_SIDE_CODES else 400)
            except Http404:
                return _error("Not found.", "not_found", 404)
            except Exception:
                log.exception("API error in %s", view.__name__)
                return _error("Something went wrong on our side. Please try again.", "server_error", 500)
            response = result if isinstance(result, (JsonResponse, FileResponse)) else JsonResponse(result)
            response["Cache-Control"] = "no-store"
            return response

        return wrapper

    return decorator


def _body(request):
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidInput("The request body isn't valid JSON.", "bad_json") from exc
    if not isinstance(data, dict):
        raise InvalidInput("The request body must be a JSON object.", "bad_json")
    return data


def _int(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise InvalidInput(f"{name} must be a whole number.", "bad_number")
    try:
        number = int(value)
    except ValueError as exc:
        raise InvalidInput(f"{name} must be a whole number.", "bad_number") from exc
    if not low <= number <= high:
        raise InvalidInput(f"{name} must be between {low} and {high}.", "bad_number")
    return number


def current_party(request, required=True):
    """The signed-in account of this browser session (sign-in with Xaman), or None / 401."""
    party_id = request.session.get("party_id")
    party = Party.objects.filter(pk=party_id).first() if party_id else None
    if party is None and required:
        raise NotSignedIn()
    return party


def _lease_for(lease_id, party):
    """A lease the account is a party of; anything else looks like it doesn't exist."""
    lease = Lease.objects.select_related("tenant", "landlord", "arbiter", "currency").filter(pk=lease_id).first()
    if lease is None or lease.role_of(party) is None:
        raise Http404
    return lease


def _require_action(lease, party, action):
    if action not in lifecycle.allowed_actions(lease, party):
        raise InvalidTransition("That isn't possible right now for your role in this deposit.", "not_allowed")


# ---- serialisation ----

ROLES = ("tenant", "landlord", "arbiter")


def _party(party, role=None):
    return {"party_id": party.pk, "name": party.label, "role": role, "address": party.address,
            "explorer_url": explorer_account(party.address)}


def _parties(lease):
    return [_party(p, role) for role, p in zip(ROLES, lease.signers()) if p is not None]


def _proposal(proposal, lease):
    complete = set(lifecycle.complete_signers(proposal))
    signers = [(role, p) for role, p in zip(ROLES, lease.signers())]
    is_open = proposal.status == SettlementProposal.Status.OPEN
    return {
        "id": proposal.pk, "status": proposal.status,
        "tenant_amount": plain(proposal.tenant_amount), "landlord_amount": plain(proposal.landlord_amount),
        "reason": proposal.reason,
        "proposed_by": _party(proposal.proposed_by, lease.role_of(proposal.proposed_by)),
        "signed_by": [_party(p, role) for role, p in signers if str(p.pk) in complete],
        "still_needed": [_party(p, role) for role, p in signers if str(p.pk) not in complete] if is_open else [],
        "signatures_count": len(complete), "signatures_required": lifecycle.QUORUM,
        "payouts_count": len(proposal.prepared_txs),
        "executed": [{"tx_hash": h, "explorer_url": explorer_tx(h)} for h in proposal.executed_hashes],
        "created_at": proposal.created_at.isoformat(),
    }


def _balances(address):
    key = f"balances:{address}"
    balances = cache.get(key)
    if balances is None:
        balances = {code: plain(value) for code, value in services.get_balances(address).items()}
        cache.set(key, balances, lifecycle.LIVE_TTL)
    return balances


def _live(lease):
    """What the ledger says right now about the settlement account and the escrow, cached for 10 s."""
    key = lifecycle.live_cache_key(lease.pk)
    live = cache.get(key)
    if live is not None:
        return live
    live = {"settlement": None, "escrow_exists": None}
    if lease.settlement_address:
        roles = {p.address: (role, p.label) for role, p in zip(ROLES, lease.signers()) if p is not None}
        settlement = {"address": lease.settlement_address, "explorer_url": explorer_account(lease.settlement_address)}
        try:
            signer_list = services.get_signer_list(lease.settlement_address) or {"quorum": None, "signers": []}
            settlement.update(
                signers=[
                    {"address": s["address"], "weight": s["weight"], "role": roles.get(s["address"], (None, None))[0],
                     "name": roles.get(s["address"], (None, None))[1]}
                    for s in signer_list["signers"]
                ],
                quorum=signer_list["quorum"],
                master_disabled=services.get_account_flags(lease.settlement_address)["disable_master"],
                balance=plain(services.get_token_balance(lease.settlement_address, lease.currency.code, lease.currency.issuer_address)),
                source="ledger",
            )
        except LedgerError as exc:
            settlement["error"] = exc.message
        live["settlement"] = settlement
    if lease.escrow_sequence is not None:
        if lease.status == Lease.Status.ACTIVE:
            try:
                live["escrow_exists"] = services.get_escrow(lease.escrow_owner, lease.escrow_sequence) is not None
            except LedgerError:
                live["escrow_exists"] = None
        else:
            live["escrow_exists"] = False  # finished or cancelled: that is how the lease left ACTIVE
    cache.set(key, live, lifecycle.LIVE_TTL)
    return live


def _event(event):
    return {
        "id": event.pk, "kind": event.kind, "summary": event.summary, "tx_hash": event.tx_hash,
        "explorer_url": explorer_tx(event.tx_hash) if event.tx_hash else None, "created_at": event.created_at.isoformat(),
    }


def _evidence(item):
    return {
        "id": item.pk, "kind": item.kind, "kind_label": item.get_kind_display(), "sha256": item.sha256,
        "url": f"/api/evidence/{item.pk}/file/" if item.file else None,  # parties only; never a public media URL
        "uploaded_by": item.uploaded_by.label, "uploaded_by_id": item.uploaded_by_id,
        "anchor_tx_hash": item.anchor_tx_hash,
        "explorer_url": explorer_tx(item.anchor_tx_hash) if item.anchor_tx_hash else None,
        "created_at": item.created_at.isoformat(),
    }


def _lease_summary(lease, party):
    return {
        "id": str(lease.pk), "title": lease.title,
        "status": lease.status, "status_label": lease.get_status_display(),
        "setup_step": lease.setup_step, "release_reason": lease.release_reason, "error": lease.error,
        "amount": plain(lease.amount), "currency": lease.currency.code, "currency_label": lease.currency.label,
        "role": lease.role_of(party), "parties": _parties(lease),
        "open_roles": [role for role, p in zip(ROLES, lease.signers()) if p is None],
        "end_at": lease.end_at.isoformat(), "grace_minutes": lease.grace_minutes,
        "cancel_after": lease.cancel_after.isoformat(), "created_at": lease.created_at.isoformat(),
    }


def _agreement(lease, party, request):
    role = lease.role_of(party)
    data = {
        "arbiter_accepted": lease.arbiter_accepted,
        "tenant_approved_arbiter": lease.tenant_approved_arbiter,
        "landlord_approved_arbiter": lease.landlord_approved_arbiter,
        "created_by": lease.created_by_id,
    }
    invites = []
    if lease.status == Lease.Status.PENDING_ACCEPTANCE and role in ("tenant", "landlord"):
        invites = [{"role": i.role, "url": request.build_absolute_uri(f"/invite/{i.token}/")} for i in agreements.open_invites(lease)]
    return data, invites


def lease_state(lease, party, request):
    """Everything the lease page shows. Never includes the fulfillment or any seed."""
    summary = _lease_summary(lease, party)
    agreement, invites = _agreement(lease, party, request)
    base = {**summary, "agreement": agreement, "invites": invites, "issuer_address": lease.currency.issuer_address,
            "network": network_key(), "me": _party(party, lease.role_of(party)),
            "events": [_event(e) for e in lease.events.all()], "actions": lifecycle.allowed_actions(lease, party)}
    if lease.status in (Lease.Status.PENDING_ACCEPTANCE, Lease.Status.CANCELLED) and not lease.settlement_address:
        return {**base, "settlement": None, "escrow": None, "seconds_to_cancel_after": None, "proposals": [], "evidence": []}
    now = lifecycle.ledger_now()
    live = _live(lease)
    escrow = None
    if lease.escrow_sequence is not None:
        escrow = {
            "owner": lease.escrow_owner, "sequence": lease.escrow_sequence, "destination": lease.settlement_address,
            "cancel_after": lease.cancel_after.isoformat(), "exists": live["escrow_exists"],
            "explorer_url": explorer_account(lease.escrow_owner),
        }
    return {
        **base, "actions": lifecycle.allowed_actions(lease, party, now),
        "settlement": live["settlement"], "escrow": escrow,
        "seconds_to_cancel_after": max(0, int((lease.cancel_after - now).total_seconds())),
        "proposals": [_proposal(p, lease) for p in lease.proposals.select_related("proposed_by")],
        "evidence": [_evidence(e) for e in lease.evidence.select_related("uploaded_by")],
    }


# ---- accounts and Xaman ----

def _me(party):
    if party is None:
        return {"signed_in": False}
    return {"signed_in": True, **_party(party), "needs_name": not party.display_name, "network": network_key()}


@api("GET", "POST")
def me(request):
    party = current_party(request, required=request.method == "POST")
    if request.method == "POST":
        name = " ".join(str(_body(request).get("display_name") or "").split())
        if not 1 <= len(name) <= 80:
            raise InvalidInput("Give a name of 1 to 80 characters.", "bad_name")
        party.display_name = name
        party.save(update_fields=["display_name"])
    return _me(party)


@api("POST")
def signin(request):
    req = signing.start_signin()
    request.session["signin_request"] = str(req.pk)  # only this browser may complete it
    return signing.present(req)


@api("POST")
def signout(request):
    request.session.flush()
    return {"ok": True}


def _request_for(request, request_id):
    req = XamanRequest.objects.select_related("party", "lease", "proposal", "evidence").filter(pk=request_id).first()
    if req is None:
        raise Http404
    if req.purpose == XamanRequest.Purpose.SIGNIN:
        if request.session.get("signin_request") != str(req.pk):
            raise Http404
    elif req.party_id != request.session.get("party_id"):
        raise Http404
    return req


@api("GET")
def xaman_request(request, request_id):
    req = signing.refresh(_request_for(request, request_id))
    data = signing.present(req)
    if req.purpose == XamanRequest.Purpose.SIGNIN and req.status == XamanRequest.Status.DONE:
        request.session.cycle_key()  # new session id once the identity changes (session fixation)
        request.session["party_id"] = req.party_id
        request.session.pop("signin_request", None)
        data.update(signed_in=True, needs_name=not req.party.display_name)
    return data


@api("POST")
def xaman_cancel(request, request_id):
    req = _request_for(request, request_id)
    if req.status == XamanRequest.Status.OPEN:
        xaman.cancel_payload(req.xaman_uuid)
        req = signing.refresh(req)  # the user may have signed first: ask Xaman and finish it
        if req.status == XamanRequest.Status.OPEN:
            XamanRequest.objects.filter(pk=req.pk, status=XamanRequest.Status.OPEN).update(status=XamanRequest.Status.REJECTED)
            req.refresh_from_db()
    return signing.present(req)


# ---- faucet ----

def _currency(data):
    currency = Currency.objects.filter(code=str(data.get("currency") or "").upper()).first()
    if currency is None:
        raise InvalidInput("Pick EUR or USD.", "bad_currency")
    return currency


@api("GET")
def faucet_status(request):
    return faucet.status(current_party(request))


@api("POST")
def faucet_activate(request):
    tx_hash = faucet.activate(current_party(request))
    return {"tx_hash": tx_hash, "explorer_url": explorer_tx(tx_hash)}


@api("POST")
def faucet_trustline(request):
    party = current_party(request)
    return signing.present(signing.start_trustline(party, _currency(_body(request))))


@api("POST")
def faucet_tokens(request):
    party = current_party(request)
    tx_hash = faucet.grant_tokens(party, _currency(_body(request)))
    return {"tx_hash": tx_hash, "explorer_url": explorer_tx(tx_hash)}


# ---- leases and agreements ----

def _parse_end(data):
    if data.get("end_in_minutes") not in (None, ""):
        return timezone.now() + timedelta(minutes=_int(data["end_in_minutes"], "end_in_minutes", 1, 60 * 24 * 365 * 5))
    raw = str(data.get("end_date") or "").strip()
    if not raw:
        raise InvalidInput("Give the lease end as end_in_minutes or end_date.", "bad_end")
    try:
        end = parse_datetime(raw)
        if end is None:
            day = parse_date(raw)
            end = datetime.combine(day, time(23, 59, 59)) if day else None
    except ValueError:
        end = None
    if end is None:
        raise InvalidInput("end_date must look like 2027-06-30 or 2027-06-30T18:00.", "bad_end")
    return timezone.make_aware(end) if timezone.is_naive(end) else end


@api("GET", "POST")
def leases(request):
    party = current_party(request)
    if request.method == "POST":
        data = _body(request)
        if data.get("role") not in ("tenant", "landlord"):
            raise InvalidInput("Choose whether you are the tenant or the landlord.", "bad_role")
        currency = _currency(data)
        amount = lifecycle.parse_amount(data.get("amount"))
        if amount <= 0:
            raise InvalidInput("The deposit must be greater than zero.", "bad_amount")
        services.token_value(amount)
        arbiter_address = str(data.get("arbiter_address") or "").strip()
        if arbiter_address and not is_valid_classic_address(arbiter_address):
            raise InvalidInput("The arbiter's address is not a valid XRP Ledger address.", "bad_address")
        title = " ".join(str(data.get("title") or "").split())
        if len(title) > 120:
            raise InvalidInput("Keep the property name under 120 characters.", "bad_title")
        lease = agreements.create_draft(
            party, data.get("role"), currency=currency, amount=amount, end_at=_parse_end(data),
            grace_minutes=_int(data.get("grace_minutes", 1440), "grace_minutes", 0, 60 * 24 * 365),
            title=title, arbiter_address=arbiter_address,
        )
        return JsonResponse({"lease_id": str(lease.pk)}, status=201)
    found = Lease.objects.filter(Q(tenant=party) | Q(landlord=party) | Q(arbiter=party)).select_related("tenant", "landlord", "arbiter", "currency")
    status = request.GET.get("status")
    if status:
        wanted = [s for s in status.upper().split(",") if s in Lease.Status.values]
        if not wanted:
            raise InvalidInput("Unknown status filter.", "bad_status")
        found = found.filter(status__in=wanted)
    return {"leases": [_lease_summary(lease, party) for lease in found[:200]]}


@api("GET")
def lease_detail(request, lease_id):
    party = current_party(request)
    return lease_state(_lease_for(lease_id, party), party, request)


@api("POST")
def lease_cancel(request, lease_id):
    party = current_party(request)
    lease = agreements.cancel_by(_lease_for(lease_id, party), party)
    return lease_state(lease, party, request)


@api("POST")
def lease_arbiter(request, lease_id):
    party = current_party(request)
    lease = agreements.change_arbiter(_lease_for(lease_id, party), party, _body(request).get("address"))
    return lease_state(lease, party, request)


@api("POST")
def lease_approve_arbiter(request, lease_id):
    party = current_party(request)
    lease = agreements.approve_arbiter(_lease_for(lease_id, party), party)
    return lease_state(_lease_for(lease.pk, party), party, request)


@api("POST")
def escrow(request, lease_id):
    party = current_party(request)
    return signing.present(signing.start_escrow(_lease_for(lease_id, party), party))


@api("POST")
def release(request, lease_id):
    party = current_party(request)
    lease = _lease_for(lease_id, party)
    reason = _body(request).get("reason")
    if reason not in Lease.ReleaseReason.values:
        raise InvalidInput("The reason must be early_move_out or damage_claim.", "bad_reason")
    _require_action(lease, party, "release_early" if reason == Lease.ReleaseReason.EARLY_MOVE_OUT else "claim_damage")
    lifecycle.release(lease, party, reason)
    return lease_state(lease, party, request)


@api("POST")
def proposals(request, lease_id):
    party = current_party(request)
    lease = _lease_for(lease_id, party)
    data = _body(request)
    _require_action(lease, party, "propose")
    proposal = lifecycle.propose(lease, party, data.get("tenant_amount"), data.get("landlord_amount"), data.get("reason", ""))
    return JsonResponse({"proposal_id": proposal.pk, "lease": lease_state(lease, party, request)}, status=201)


def _proposal_for(proposal_id, party):
    proposal = SettlementProposal.objects.select_related("lease").filter(pk=proposal_id).first()
    if proposal is None:
        raise Http404
    _lease_for(proposal.lease_id, party)
    return proposal


@api("POST")
def proposal_sign(request, proposal_id):
    party = current_party(request)
    proposal = _proposal_for(proposal_id, party)
    req = signing.start_payout(proposal, party)
    if req is None:
        return {"done": True, "lease": lease_state(_lease_for(proposal.lease_id, party), party, request)}
    return signing.present(req)


@api("POST")
def proposal_retry(request, proposal_id):
    party = current_party(request)
    proposal = lifecycle.retry_payout(_proposal_for(proposal_id, party), party)
    return {"lease": lease_state(_lease_for(proposal.lease_id, party), party, request)}


@api("POST")
def evidence(request, lease_id):
    """Store a photo and its SHA-256. Anchoring the hash is a separate Xaman request (evidence_anchor)."""
    party = current_party(request)
    lease = _lease_for(lease_id, party)
    _require_action(lease, party, "upload_evidence")
    kind, upload = request.POST.get("kind"), request.FILES.get("file")
    if kind not in Evidence.Kind.values:
        raise InvalidInput("kind must be move_in, move_out or damage.", "bad_kind")
    if upload is None:
        raise InvalidInput("Attach a photo in the 'file' field.", "no_file")
    if upload.size > MAX_EVIDENCE_BYTES:
        raise InvalidInput("Photos can be at most 5 MB.", "file_too_large")
    extension = ("." + upload.name.rsplit(".", 1)[-1].lower()) if "." in upload.name else ""
    head = upload.read(12)
    if extension not in EVIDENCE_TYPES or not EVIDENCE_TYPES[extension](head):
        raise InvalidInput("Upload a JPEG, PNG or WebP photo.", "bad_file_type")
    digest = hashlib.sha256()
    for chunk in upload.chunks():  # chunks() starts again from the beginning of the file
        digest.update(chunk)
    upload.seek(0)
    item = Evidence(lease=lease, kind=kind, sha256=digest.hexdigest(), uploaded_by=party)
    item.file.save(f"{uuid.uuid4().hex}{extension}", upload)  # never trust the uploaded file name
    return JsonResponse(_evidence(item), status=201)  # the browser then asks for /api/evidence/<id>/anchor/


def _evidence_for(evidence_id, party):
    item = Evidence.objects.select_related("lease", "uploaded_by").filter(pk=evidence_id).first()
    if item is None or item.lease.role_of(party) is None:
        raise Http404
    return item


@api("GET")
def evidence_file(request, evidence_id):
    """The photo itself, for the lease's three parties only. Served as an image and nothing else."""
    item = _evidence_for(evidence_id, current_party(request))
    extension = "." + item.file.name.rsplit(".", 1)[-1].lower()
    if extension not in EVIDENCE_CONTENT_TYPES or not item.file.storage.exists(item.file.name):
        raise Http404
    response = FileResponse(item.file.open("rb"), content_type=EVIDENCE_CONTENT_TYPES[extension])
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Disposition"] = f'inline; filename="evidence-{item.pk}{extension}"'
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


@api("POST")
def evidence_anchor(request, evidence_id):
    party = current_party(request)
    return signing.present(signing.start_anchor(_evidence_for(evidence_id, party), party))


@api("GET")
def lease_public(request, lease_id):
    """For the public verify page: where to look on the ledger. Addresses and the escrow sequence only."""
    lease = Lease.objects.select_related("tenant", "landlord", "arbiter", "currency").filter(pk=lease_id).first()
    if lease is None or lease.status == Lease.Status.PENDING_ACCEPTANCE:
        raise Http404
    net = network()
    return {
        "lease_id": str(lease.pk), "network": network_key(), "ws": net["ws"], "explorer": net["explorer"],
        "tenant_address": getattr(lease.tenant, "address", ""), "landlord_address": getattr(lease.landlord, "address", ""),
        "arbiter_address": getattr(lease.arbiter, "address", ""), "settlement_address": lease.settlement_address,
        "escrow_owner": lease.escrow_owner, "escrow_sequence": lease.escrow_sequence,
        "currency": lease.currency.code, "issuer_address": lease.currency.issuer_address,
        # Fingerprints only (they are public on the ledger already); the photos stay private.
        "evidence": [
            {"kind": e.kind, "sha256": e.sha256, "anchor_tx_hash": e.anchor_tx_hash} for e in lease.evidence.all() if e.anchor_tx_hash
        ],
    }


# ---- invites ----

@api("GET")
def invite(request, token):
    item = agreements.invite_for(token)
    lease, party = item.lease, current_party(request, required=False)
    used = bool(item.accepted_by_id or item.declined_at)
    by_you = bool(party and item.accepted_by_id == party.pk)
    if used and not by_you:  # a spent link reveals nothing about the agreement
        return {
            "role": item.role, "used": True, "accepted_by_you": False, "signed_in": party is not None,
            "already_party": bool(party and lease.role_of(party)),
            "lease": {"status": lease.status, "status_label": lease.get_status_display()},
        }
    return {
        "role": item.role, "used": used,
        "accepted_by_you": by_you,
        "signed_in": party is not None, "already_party": bool(party and lease.role_of(party) and item.accepted_by_id != party.pk),
        "lease": {
            "id": str(lease.pk), "title": lease.title, "status": lease.status, "status_label": lease.get_status_display(),
            "amount": plain(lease.amount), "currency": lease.currency.code, "currency_label": lease.currency.label,
            "end_at": lease.end_at.isoformat(), "grace_minutes": lease.grace_minutes, "cancel_after": lease.cancel_after.isoformat(),
            "parties": _parties(lease), "created_by": lease.created_by.label,
        },
    }


@api("POST")
def invite_accept(request, token):
    lease = agreements.accept_invite(token, current_party(request))
    return {"lease_id": str(lease.pk)}


@api("POST")
def invite_decline(request, token):
    agreements.decline_invite(token, current_party(request))
    return {"ok": True}


# ---- settlement inspector ----

@api("GET")
def settlement_raw(request, lease_id):
    """The settlement account exactly as the validated ledger holds it (parties only)."""
    party = current_party(request)
    lease = _lease_for(lease_id, party)
    if not lease.settlement_address:
        raise InvalidTransition("The settlement account doesn't exist yet.", "wrong_status")
    return {
        "address": lease.settlement_address, "explorer_url": explorer_account(lease.settlement_address),
        "currency": lease.currency.code, "issuer_address": lease.currency.issuer_address,
        "raw": services.get_account_raw(lease.settlement_address),
    }
