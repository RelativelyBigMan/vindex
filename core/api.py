"""JSON API (the contract the frontend is built against).

Errors are always {"error": friendly message, "code": engine result or internal code} with
HTTP 400 (bad input, ledger refusal), 409 (not allowed in this state / for this persona) or
500 (our fault, or the ledger can't be reached). Every POST needs Django's CSRF token in X-CSRFToken.

Secrets: the fulfillment leaves the server only in GET .../release-tx/ for the lease's tenant
or landlord; seeds never leave it.
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from xrpl.core.addresscodec import is_valid_classic_address

from ledger import services
from ledger.client import explorer_account, explorer_tx, network, network_key
from ledger.errors import LedgerError

from . import lifecycle
from .lifecycle import InvalidInput, InvalidTransition, plain
from .models import Currency, Evidence, Lease, Party, PlatformWallet, SettlementProposal

log = logging.getLogger(__name__)

MIN_SECONDS_TO_RETURN = 90  # setup takes about 20 s; the deposit must still be lockable afterwards
MAX_EVIDENCE_BYTES = 5 * 1024 * 1024
EVIDENCE_TYPES = {  # extension -> file signature check
    ".jpg": lambda head: head[:3] == b"\xff\xd8\xff",
    ".jpeg": lambda head: head[:3] == b"\xff\xd8\xff",
    ".png": lambda head: head[:8] == b"\x89PNG\r\n\x1a\n",
    ".webp": lambda head: head[:4] == b"RIFF" and head[8:12] == b"WEBP",
}
FUND_XRP = Decimal("10")
FUND_XRP_BELOW = Decimal("15")
FUND_TOKENS = Decimal("5000")
SERVER_SIDE_CODES = {"network_error", "unknown"}


# ---- plumbing ----

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
            except InvalidTransition as exc:
                return _error(exc.message, exc.code, 409)
            except LedgerError as exc:
                return _error(exc.message, exc.engine_result, 500 if exc.engine_result in SERVER_SIDE_CODES else 400)
            except Http404:
                return _error("Not found.", "not_found", 404)
            except Exception:
                log.exception("API error in %s", view.__name__)
                return _error("Something went wrong on our side. Please try again.", "server_error", 500)
            response = result if isinstance(result, JsonResponse) else JsonResponse(result)
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


def _require_demo():
    if not settings.DEMO_MODE:
        raise InvalidTransition("Demo mode is switched off on this server.", "demo_disabled")


def current_party(request):
    """The persona this session acts as. Demo mode has no passwords and defaults to the tenant persona."""
    party = Party.objects.filter(pk=request.session.get("party_id")).first() if request.session.get("party_id") else None
    if party is not None and party.wallet_type == Party.WalletType.DEMO and not settings.DEMO_MODE:
        party = None
    if party is None and settings.DEMO_MODE:
        party = Party.objects.filter(role=Party.Role.TENANT, wallet_type=Party.WalletType.DEMO).order_by("pk").first()
    if party is None:
        raise InvalidTransition("No persona is selected. Run bootstrap_demo, then pick one on the demo page.", "no_persona")
    return party


def _lease_for(lease_id, party):
    """A lease the persona is a party of; anything else looks like it doesn't exist."""
    lease = Lease.objects.select_related("tenant", "landlord", "arbiter", "currency").filter(pk=lease_id).first()
    if lease is None or lease.role_of(party) is None:
        raise Http404
    return lease


def _require_action(lease, party, action):
    if action not in lifecycle.allowed_actions(lease, party):
        raise InvalidTransition("That isn't possible right now for the persona you are acting as.", "not_allowed")


# ---- serialisation ----

def _party(party, role=None):
    return {
        "party_id": party.pk, "name": party.display_name, "role": role or party.role, "address": party.address,
        "wallet_type": party.wallet_type, "explorer_url": explorer_account(party.address),
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
        roles = {p.address: (role, p.display_name) for role, p in zip(("tenant", "landlord", "arbiter"), lease.signers())}
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


def _proposal(proposal, lease):
    signed_ids = set(proposal.signatures)
    signers = [(role, p) for role, p in zip(("tenant", "landlord", "arbiter"), lease.signers())]
    is_open = proposal.status == SettlementProposal.Status.OPEN
    return {
        "id": proposal.pk, "status": proposal.status,
        "tenant_amount": plain(proposal.tenant_amount), "landlord_amount": plain(proposal.landlord_amount),
        "reason": proposal.reason,
        "proposed_by": _party(proposal.proposed_by, lease.role_of(proposal.proposed_by)),
        "signed_by": [_party(p, role) for role, p in signers if str(p.pk) in signed_ids],
        "still_needed": [_party(p, role) for role, p in signers if str(p.pk) not in signed_ids] if is_open else [],
        "signatures_count": len(signed_ids), "signatures_required": lifecycle.QUORUM,
        "executed": [{"tx_hash": h, "explorer_url": explorer_tx(h)} for h in proposal.executed_hashes],
        "created_at": proposal.created_at.isoformat(),
    }


def _event(event):
    return {
        "id": event.pk, "kind": event.kind, "summary": event.summary, "tx_hash": event.tx_hash,
        "explorer_url": explorer_tx(event.tx_hash) if event.tx_hash else None, "created_at": event.created_at.isoformat(),
    }


def _evidence(item):
    return {
        "id": item.pk, "kind": item.kind, "sha256": item.sha256, "url": item.file.url if item.file else None,
        "uploaded_by": item.uploaded_by.display_name, "anchor_tx_hash": item.anchor_tx_hash,
        "explorer_url": explorer_tx(item.anchor_tx_hash) if item.anchor_tx_hash else None,
        "created_at": item.created_at.isoformat(),
    }


def _lease_summary(lease, party):
    return {
        "id": str(lease.pk), "status": lease.status, "status_label": lease.get_status_display(),
        "setup_step": lease.setup_step, "release_reason": lease.release_reason, "error": lease.error,
        "amount": plain(lease.amount), "currency": lease.currency.code, "currency_label": lease.currency.label,
        "role": lease.role_of(party),
        "parties": [_party(p, role) for role, p in zip(("tenant", "landlord", "arbiter"), lease.signers())],
        "end_at": lease.end_at.isoformat(), "grace_minutes": lease.grace_minutes,
        "cancel_after": lease.cancel_after.isoformat(), "created_at": lease.created_at.isoformat(),
    }


def lease_state(lease, party):
    """Everything the lease page shows. Never includes the fulfillment or any seed."""
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
        **_lease_summary(lease, party),
        "issuer_address": lease.currency.issuer_address,
        "network": network_key(),
        "settlement": live["settlement"],
        "escrow": escrow,
        "seconds_to_cancel_after": max(0, int((lease.cancel_after - now).total_seconds())),
        "proposals": [_proposal(p, lease) for p in lease.proposals.select_related("proposed_by")],
        "events": [_event(e) for e in lease.events.all()],
        "evidence": [_evidence(e) for e in lease.evidence.select_related("uploaded_by")],
        "actions": lifecycle.allowed_actions(lease, party, now),
    }


# ---- persona and demo helpers ----

@api("GET")
def me(request):
    party = current_party(request)
    data = {**_party(party), "network": network_key(), "demo_mode": settings.DEMO_MODE, "balances": None}
    try:
        data["balances"] = _balances(party.address)
    except LedgerError as exc:
        data["balances_error"] = exc.message
    return data


@api("GET")
def demo_personas(request):
    """Not in the contract: the personas a demo session can switch between (ids for the persona switcher)."""
    _require_demo()
    parties = Party.objects.filter(wallet_type=Party.WalletType.DEMO).order_by("pk")
    return {"personas": [_party(p) for p in parties], "current": current_party(request).pk}


@api("POST")
def demo_persona(request):
    _require_demo()
    party_id = _int(_body(request).get("party_id"), "party_id", 1, 2**31)
    party = Party.objects.filter(pk=party_id, wallet_type=Party.WalletType.DEMO).first()
    if party is None:
        raise InvalidInput("That demo persona doesn't exist.", "unknown_party")
    request.session["party_id"] = party.pk
    return _party(party)


@api("POST")
def demo_fund(request):
    """Top the current persona up with XRP (when low) and 5,000 of each test token."""
    _require_demo()
    party = current_party(request)
    if not cache.add(f"fund-cooldown:{party.address}", 1, 30):
        raise InvalidTransition("This persona was topped up a moment ago. Wait half a minute.", "too_soon")
    hashes, skipped = [], []
    if services.get_balances(party.address)["XRP"] < FUND_XRP_BELOW:
        hashes.append(services.fund_xrp(lifecycle.platform_wallet("treasury"), party.address, FUND_XRP))
    issuers = {w.address: w for w in PlatformWallet.objects.filter(role__startswith="issuer_")}
    for currency in Currency.objects.order_by("code"):
        issuer = issuers.get(currency.issuer_address)
        if issuer is None:
            skipped.append(currency.code)
            continue
        if party.seed:
            services.ensure_trust_line(lifecycle.wallet_for(party), currency.code, currency.issuer_address)
        elif not services.has_trust_line(party.address, currency.code, currency.issuer_address):
            skipped.append(currency.code)  # an external wallet has to add its own trust line first
            continue
        hashes.append(services.issue_tokens(lifecycle.Wallet.from_seed(issuer.seed), party.address, currency.code, FUND_TOKENS))
    cache.delete(f"balances:{party.address}")
    return {
        "tx_hashes": hashes, "explorer_urls": [explorer_tx(h) for h in hashes], "skipped": skipped,
        "balances": _balances(party.address),
    }


@api("POST")
def demo_run_bot(request):
    _require_demo()
    return {"processed": lifecycle.bot_pass()}


@api("POST")
def wallet_link(request):
    """Link a browser wallet's address to a Party (no seed on the server) and act as it.

    Ownership of the address is NOT proven here; Patch 5 adds the signed challenge.
    """
    data = _body(request)
    address, role = str(data.get("address") or "").strip(), data.get("role")
    if not is_valid_classic_address(address):
        raise InvalidInput("That is not a valid XRP Ledger address.", "bad_address")
    if role not in Party.Role.values:
        raise InvalidInput("The role must be tenant, landlord or arbiter.", "bad_role")
    party = Party.objects.filter(address=address).first()
    if party is None:
        party = Party.objects.create(
            display_name=f"Wallet {address[:6]}…{address[-4:]}", role=role, address=address, seed=None,
            wallet_type=Party.WalletType.GEMWALLET,
        )
    elif party.wallet_type == Party.WalletType.DEMO and not settings.DEMO_MODE:
        raise InvalidTransition("That address belongs to a demo persona.", "not_allowed")
    elif party.role != role:
        raise InvalidTransition(f"That address is already registered as a {party.role}.", "role_mismatch")
    request.session["party_id"] = party.pk
    return _party(party)


# ---- leases ----

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


def _create_lease(request, tenant):
    data = _body(request)
    if tenant.role != Party.Role.TENANT:
        raise InvalidTransition("Only a tenant can start a deposit. Switch to the tenant persona.", "not_allowed")
    landlord = Party.objects.filter(pk=_int(data.get("landlord_id"), "landlord_id", 1, 2**31), role=Party.Role.LANDLORD).first()
    arbiter = Party.objects.filter(pk=_int(data.get("arbiter_id"), "arbiter_id", 1, 2**31), role=Party.Role.ARBITER).first()
    if landlord is None or arbiter is None:
        raise InvalidInput("Pick an existing landlord and an existing arbiter.", "unknown_party")
    if len({tenant.address, landlord.address, arbiter.address}) != 3:
        raise InvalidInput("Tenant, landlord and arbiter must be three different accounts.", "bad_signers")
    currency = Currency.objects.filter(code=str(data.get("currency") or "").upper()).first()
    if currency is None:
        raise InvalidInput("Pick EUR or USD.", "bad_currency")
    amount = lifecycle.parse_amount(data.get("amount"))
    if amount <= 0:
        raise InvalidInput("The deposit must be greater than zero.", "bad_amount")
    services.token_value(amount)  # refuses amounts the ledger can't hold exactly
    grace = _int(data.get("grace_minutes", 1), "grace_minutes", 0, 60 * 24 * 365)
    end_at = _parse_end(data)
    if end_at + timedelta(minutes=grace) < timezone.now() + timedelta(seconds=MIN_SECONDS_TO_RETURN):
        raise InvalidInput(
            f"The lease end plus the grace period must be at least {MIN_SECONDS_TO_RETURN} seconds from now, "
            "or there is no time to lock the deposit.", "bad_end",
        )
    lease = Lease.objects.create(
        tenant=tenant, landlord=landlord, arbiter=arbiter, currency=currency, amount=amount, end_at=end_at, grace_minutes=grace
    )
    lifecycle.start_setup(lease)
    return JsonResponse({"lease_id": str(lease.pk)}, status=201)


@api("GET", "POST")
def leases(request):
    party = current_party(request)
    if request.method == "POST":
        return _create_lease(request, party)
    found = (
        Lease.objects.filter(Q(tenant=party) | Q(landlord=party) | Q(arbiter=party), archived=False)
        .select_related("tenant", "landlord", "arbiter", "currency")
    )
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
    return lease_state(_lease_for(lease_id, party), party)


@api("GET")
def lease_public(request, lease_id):
    """For the public verify page: where to look on the ledger. Addresses and the escrow sequence only."""
    lease = Lease.objects.select_related("tenant", "landlord", "arbiter", "currency").filter(pk=lease_id).first()
    if lease is None:
        raise Http404
    net = network()
    return {
        "lease_id": str(lease.pk), "network": network_key(), "ws": net["ws"], "explorer": net["explorer"],
        "tenant_address": lease.tenant.address, "landlord_address": lease.landlord.address,
        "arbiter_address": lease.arbiter.address, "settlement_address": lease.settlement_address,
        "escrow_owner": lease.escrow_owner, "escrow_sequence": lease.escrow_sequence,
        "currency": lease.currency.code, "issuer_address": lease.currency.issuer_address,
    }


@api("GET")
def escrow_tx(request, lease_id):
    """Unsigned EscrowCreate for the tenant's own wallet to sign."""
    party = current_party(request)
    lease = _lease_for(lease_id, party)
    _require_action(lease, party, "lock_deposit")
    return {
        "tx": services.build_escrow_create(
            lease.tenant.address, lease.settlement_address, lease.currency.code, lease.currency.issuer_address,
            lease.amount, lease.condition_hex, lease.cancel_after,
        )
    }


@api("POST")
def escrow(request, lease_id):
    party = current_party(request)
    lease = _lease_for(lease_id, party)
    data = _body(request)
    _require_action(lease, party, "lock_deposit")
    mode = data.get("mode", "demo")
    if mode == "demo":
        _require_demo()
        lifecycle.create_escrow_demo(lease)
    elif mode == "wallet":
        lifecycle.register_wallet_escrow(lease, data.get("tx_hash"))
    else:
        raise InvalidInput("mode must be 'demo' or 'wallet'.", "bad_mode")
    return lease_state(lease, party)


@api("GET")
def release_tx(request, lease_id):
    """Unsigned EscrowFinish INCLUDING THE FULFILLMENT, for the lease's tenant or landlord only."""
    party = current_party(request)
    lease = _lease_for(lease_id, party)
    if lease.role_of(party) not in ("tenant", "landlord") or lease.status != Lease.Status.ACTIVE:
        raise InvalidTransition("Only the tenant or the landlord can release a locked deposit.", "not_allowed")
    return {
        "tx": services.build_escrow_finish(
            party.address, lease.escrow_owner, lease.escrow_sequence, lease.condition_hex, lease.fulfillment_hex, lease.cancel_after
        )
    }


@api("POST")
def release(request, lease_id):
    party = current_party(request)
    lease = _lease_for(lease_id, party)
    data = _body(request)
    reason = data.get("reason")
    if reason not in Lease.ReleaseReason.values:
        raise InvalidInput("The reason must be early_move_out or damage_claim.", "bad_reason")
    _require_action(lease, party, "release_early" if reason == Lease.ReleaseReason.EARLY_MOVE_OUT else "claim_damage")
    mode = data.get("mode", "demo")
    if mode == "wallet":
        return _error("Releasing from your own wallet arrives with wallet signing (Patch 5).", "not_implemented", 501)
    if mode != "demo":
        raise InvalidInput("mode must be 'demo' or 'wallet'.", "bad_mode")
    _require_demo()
    lifecycle.release(lease, party, reason)
    return lease_state(lease, party)


@api("POST")
def proposals(request, lease_id):
    party = current_party(request)
    lease = _lease_for(lease_id, party)
    data = _body(request)
    _require_action(lease, party, "propose")
    proposal = lifecycle.propose(lease, party, data.get("tenant_amount"), data.get("landlord_amount"), data.get("reason", ""))
    return JsonResponse({"proposal_id": proposal.pk, "lease": lease_state(lease, party)}, status=201)


@api("POST")
def proposal_sign(request, proposal_id):
    party = current_party(request)
    proposal = SettlementProposal.objects.select_related("lease").filter(pk=proposal_id).first()
    if proposal is None:
        raise Http404
    lease = _lease_for(proposal.lease_id, party)
    proposal = lifecycle.sign(proposal, party)
    lease.refresh_from_db()  # sign() moved the status on its own copy of the lease
    return {"proposal_id": proposal.pk, "status": proposal.status, "lease": lease_state(lease, party)}


@api("POST")
def evidence(request, lease_id):
    """Store a photo and its SHA-256. Anchoring the hash on the ledger comes with Patch 6."""
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
    return JsonResponse(_evidence(item), status=201)
