"""Deposit agreements before anything goes on the ledger.

Tenant or landlord starts a draft; the other side and the arbiter join through single-use invite
links. Tenant and landlord must both approve the arbiter, and the arbiter must accept the role.
When all of that is true the lease moves to DRAFT and the settlement account is created.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from xrpl.core.addresscodec import is_valid_classic_address

from . import lifecycle
from .lifecycle import InvalidInput, InvalidTransition
from .models import Invite, Lease, Party

log = logging.getLogger(__name__)
S = Lease.Status
MIN_SECONDS_TO_RETURN = 600  # setup takes about 20 s; the tenant needs time to lock afterwards
SIDES = ("tenant", "landlord")


def _other(role):
    return "landlord" if role == "tenant" else "tenant"


def create_draft(creator, role, *, currency, amount, end_at, grace_minutes, title="", arbiter_address=""):
    if role not in SIDES:
        raise InvalidInput("Choose whether you are the tenant or the landlord.", "bad_role")
    if end_at + timedelta(minutes=grace_minutes) < timezone.now() + timedelta(seconds=MIN_SECONDS_TO_RETURN):
        raise InvalidInput("The lease end plus the grace period must be at least 10 minutes from now.", "bad_end")
    with transaction.atomic():
        lease = Lease(created_by=creator, currency=currency, amount=amount, end_at=end_at,
                      grace_minutes=grace_minutes, title=title, status=S.PENDING_ACCEPTANCE)
        setattr(lease, role, creator)
        lease.save()
        Invite.objects.create(lease=lease, role=_other(role))
        Invite.objects.create(lease=lease, role=Invite.Role.ARBITER)
        lifecycle.record(lease, "created", f"{creator.label} started the agreement as {role}")
        if arbiter_address:
            _name_arbiter(lease, creator, role, arbiter_address)
    return lease


def invite_for(token):
    invite = Invite.objects.select_related("lease", "lease__currency").filter(token=str(token or "")).first()
    if invite is None or invite.revoked:
        raise InvalidInput("This invite link isn't valid any more. Ask for a new one.", "invite_unknown")
    return invite


def open_invites(lease):
    return [i for i in lease.invites.all() if not (i.revoked or i.accepted_by_id or i.declined_at)]


def _require_pending(lease, doing):
    if lease.status != S.PENDING_ACCEPTANCE:
        raise InvalidTransition(f"You can't {doing} any more: this agreement is '{lease.get_status_display().lower()}'.", "wrong_status")


def accept_invite(token, party, expected_arbiter=None):
    """Accept an invite. `expected_arbiter` is the arbiter address the person saw ("" for none); when given and the
    arbiter changed since, nothing is accepted (accepting as tenant or landlord also approves the arbiter)."""
    invite = invite_for(token)
    if invite.accepted_by_id == party.pk:
        lease = invite.lease
        _maybe_start(lease)  # a retry after a failed start completes it
        return lease
    if invite.accepted_by_id or invite.declined_at:
        raise InvalidTransition("This invite has already been used.", "invite_used")
    with transaction.atomic():
        lease = Lease.objects.select_for_update().get(pk=invite.lease_id)
        _require_pending(lease, "accept")
        if expected_arbiter is not None:
            arbiter_now = lease.arbiter.address if lease.arbiter_id else ""
            if str(expected_arbiter).strip() != arbiter_now:
                raise InvalidTransition("The arbiter changed since you opened this page. Review the terms again.", "terms_changed")
        current = lease.role_of(party)
        if invite.role in SIDES:
            if current is not None:
                raise InvalidTransition("You already have a role in this agreement.", "already_party")
            if getattr(lease, f"{invite.role}_id"):
                raise InvalidTransition("Someone has already taken that role.", "role_taken")
            setattr(lease, invite.role, party)
            if lease.arbiter_id:
                setattr(lease, f"{invite.role}_approved_arbiter", True)
            what = f"accepted the agreement as {invite.role}"
        else:
            if lease.arbiter_id and lease.arbiter_id != party.pk:
                raise InvalidTransition("This agreement names a different arbiter.", "wrong_arbiter")
            if current not in (None, "arbiter"):
                raise InvalidTransition("The tenant or landlord can't also be the arbiter.", "already_party")
            lease.arbiter, lease.arbiter_accepted = party, True
            what = "accepted the arbiter role"
        lease.save()
        invite.accepted_by, invite.accepted_at = party, timezone.now()
        invite.save(update_fields=["accepted_by", "accepted_at"])
        lifecycle.record(lease, "accepted", f"{party.label} {what}")
    _maybe_start(lease)
    return lease


def decline_invite(token, party):
    invite = invite_for(token)
    if invite.accepted_by_id:
        raise InvalidTransition("This invite has already been accepted.", "invite_used")
    if invite.declined_at:
        raise InvalidTransition("This invite has already been declined.", "invite_used")
    lease = Lease.objects.get(pk=invite.lease_id)
    _require_pending(lease, "decline")
    current = lease.role_of(party)
    if invite.role == Invite.Role.ARBITER:
        if lease.arbiter_id and lease.arbiter_id != party.pk:
            raise InvalidTransition("This agreement names a different arbiter.", "wrong_arbiter")
        if current not in (None, "arbiter"):
            raise InvalidTransition("You already have a role in this agreement.", "already_party")
    elif current is not None:
        raise InvalidTransition("You already have a role in this agreement.", "already_party")
    if not lifecycle.cancel(lease, f"{party.label} declined the {invite.role} invite.", "declined"):
        raise InvalidTransition("This agreement can't be declined any more.", "wrong_status")
    invite.declined_at = timezone.now()
    invite.save(update_fields=["declined_at"])
    lease.refresh_from_db()
    return lease


def _name_arbiter(lease, actor, actor_role, address):
    address = str(address or "").strip()
    if not is_valid_classic_address(address):
        raise InvalidInput("The arbiter's address is not a valid XRP Ledger address.", "bad_address")
    arbiter, _ = Party.objects.get_or_create(address=address)
    if arbiter.pk in (lease.tenant_id, lease.landlord_id):
        raise InvalidInput("The arbiter must be someone other than the tenant and the landlord.", "bad_signers")
    lease.arbiter, lease.arbiter_accepted = arbiter, False
    lease.tenant_approved_arbiter = lease.landlord_approved_arbiter = False
    setattr(lease, f"{actor_role}_approved_arbiter", True)
    lease.save()
    lease.invites.filter(role=Invite.Role.ARBITER, revoked=False).update(revoked=True)
    Invite.objects.create(lease=lease, role=Invite.Role.ARBITER)
    lifecycle.record(lease, "arbiter_named", f"{actor.label} proposed {arbiter.label} as arbiter")


def change_arbiter(lease, party, address):
    with transaction.atomic():
        lease = Lease.objects.select_for_update().get(pk=lease.pk)
        role = lease.role_of(party)
        if role not in SIDES:
            raise InvalidTransition("Only the tenant or the landlord can choose the arbiter.", "not_allowed")
        _require_pending(lease, "change the arbiter")
        _name_arbiter(lease, party, role, address)
    return lease


def approve_arbiter(lease, party):
    role = lease.role_of(party)
    if role not in SIDES:
        raise InvalidTransition("Only the tenant or the landlord approves the arbiter.", "not_allowed")
    with transaction.atomic():
        lease = Lease.objects.select_for_update().get(pk=lease.pk)
        _require_pending(lease, "approve the arbiter")
        if not lease.arbiter_id:
            raise InvalidTransition("No arbiter has been named yet.", "no_arbiter")
        setattr(lease, f"{role}_approved_arbiter", True)
        lease.save(update_fields=[f"{role}_approved_arbiter", "updated_at"])
        lifecycle.record(lease, "arbiter_approved", f"{party.label} ({role}) approved {lease.arbiter.label} as arbiter")
    _maybe_start(lease)
    return lease


def cancel_by(lease, party):
    if lease.created_by_id != party.pk:
        raise InvalidTransition("Only the person who started this agreement can cancel it.", "not_allowed")
    if not lifecycle.cancel(lease, f"{party.label} cancelled the agreement."):
        raise InvalidTransition("This agreement can't be cancelled any more.", "wrong_status")
    lease.refresh_from_db()
    return lease


def _maybe_start(lease):
    lease.refresh_from_db()
    ready = (
        lease.status == S.PENDING_ACCEPTANCE and lease.tenant_id and lease.landlord_id and lease.arbiter_id
        and lease.arbiter_accepted and lease.tenant_approved_arbiter and lease.landlord_approved_arbiter
    )
    if not ready:
        return
    if lease.cancel_after < timezone.now() + timedelta(seconds=MIN_SECONDS_TO_RETURN):
        try:
            lifecycle.cancel(lease, "Everyone accepted, but too close to the lease end to lock the deposit safely.", "expired")
        except InvalidTransition as exc:
            if exc.code != "busy":
                raise
            log.info("Lease %s: cancel is busy; a later call will finish it.", lease.pk)
        return
    if lifecycle._advance(lease, S.PENDING_ACCEPTANCE, S.DRAFT):
        lifecycle.record(lease, "all_accepted", "Tenant, landlord and arbiter accepted. Creating the settlement account.")
        lifecycle.start_setup(lease)
