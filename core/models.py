"""Models: platform wallets, accounts, test currencies, leases, invites, Xaman requests and the timeline.

Only PLATFORM wallets keep seeds here (Testnet/Devnet only; ledger/client.py refuses mainnet).
Users sign with their own Xaman wallet; the server never holds their keys.
"""
import secrets
import uuid
from datetime import timedelta

from django.db import models

from ledger.conditions import make_condition


class PlatformWallet(models.Model):
    class Role(models.TextChoices):
        TREASURY = "treasury", "Treasury"
        BOT = "bot", "Bot"
        ISSUER_EUR = "issuer_eur", "EUR issuer"
        ISSUER_USD = "issuer_usd", "USD issuer"

    role = models.CharField(max_length=32, choices=Role.choices, unique=True)
    address = models.CharField(max_length=64, unique=True)
    seed = models.CharField(max_length=64)

    def __str__(self):
        return f"{self.get_role_display()} ({self.address})"


class Party(models.Model):
    """An account: one XRPL address, signed in with Xaman. Its role comes from each lease."""

    address = models.CharField(max_length=64, unique=True)
    display_name = models.CharField(max_length=80, blank=True, help_text="Shown to the other parties. Never sent to the ledger.")
    xaman_user_token = models.CharField(max_length=80, blank=True, help_text="Lets later sign requests arrive as push notifications.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "parties"

    @property
    def label(self):
        return self.display_name or f"{self.address[:6]}…{self.address[-4:]}"

    def __str__(self):
        return self.label


class Currency(models.Model):
    code = models.CharField(max_length=3, unique=True)
    label = models.CharField(max_length=32)
    issuer_address = models.CharField(max_length=64)

    class Meta:
        verbose_name_plural = "currencies"

    def __str__(self):
        return self.label


class Lease(models.Model):
    class Status(models.TextChoices):
        PENDING_ACCEPTANCE = "PENDING_ACCEPTANCE", "Waiting for acceptance"
        DRAFT = "DRAFT", "Being set up"
        SETTLEMENT_READY = "SETTLEMENT_READY", "Ready to lock"
        ACTIVE = "ACTIVE", "Locked"
        RETURNED = "RETURNED", "Returned"
        IN_SETTLEMENT = "IN_SETTLEMENT", "In settlement"
        SETTLED = "SETTLED", "Settled"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    class ReleaseReason(models.TextChoices):
        EARLY_MOVE_OUT = "early_move_out", "Early move-out"
        DAMAGE_CLAIM = "damage_claim", "Damage claim"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="leases_as_tenant", null=True, blank=True)
    landlord = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="leases_as_landlord", null=True, blank=True)
    arbiter = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="leases_as_arbiter", null=True, blank=True)
    created_by = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="leases_created")
    arbiter_accepted = models.BooleanField(default=False)
    tenant_approved_arbiter = models.BooleanField(default=False)
    landlord_approved_arbiter = models.BooleanField(default=False)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=20, decimal_places=6)
    title = models.CharField(max_length=120, blank=True, help_text="A label such as the property name. Never sent to the ledger.")
    end_at = models.DateTimeField()
    grace_minutes = models.PositiveIntegerField(default=1)
    cancel_after = models.DateTimeField(help_text="end_at + grace: from then on the deposit returns to the tenant.")

    settlement_address = models.CharField(max_length=64, blank=True)
    settlement_seed = models.CharField(max_length=64, blank=True, help_text="Testnet only; the master key is disabled.")
    condition_hex = models.CharField(max_length=128, blank=True)
    fulfillment_hex = models.CharField(max_length=128, blank=True, help_text="SECRET: releases the escrow.")
    escrow_owner = models.CharField(max_length=64, blank=True)
    escrow_sequence = models.BigIntegerField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_ACCEPTANCE, db_index=True)
    setup_step = models.CharField(max_length=20, blank=True)
    release_reason = models.CharField(max_length=20, choices=ReleaseReason.choices, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Lease {str(self.id)[:8]} ({self.status})"

    def save(self, *args, **kwargs):
        # Ledger time has whole seconds only, so the deadline must too.
        self.end_at = self.end_at.replace(microsecond=0)
        self.cancel_after = self.end_at + timedelta(minutes=self.grace_minutes)
        if not self.condition_hex:
            self.condition_hex, self.fulfillment_hex = make_condition()
        super().save(*args, **kwargs)

    def signers(self):
        """The three parties whose signatures count, in a fixed order (all set once setup starts)."""
        return [self.tenant, self.landlord, self.arbiter]

    def role_of(self, party):
        """'tenant' | 'landlord' | 'arbiter' for a party of this lease, else None."""
        if party is None:
            return None
        for role in ("tenant", "landlord", "arbiter"):
            if getattr(self, f"{role}_id") == party.pk:
                return role
        return None


def new_invite_token():
    return secrets.token_urlsafe(32)


class Invite(models.Model):
    class Role(models.TextChoices):
        TENANT = "tenant", "Tenant"
        LANDLORD = "landlord", "Landlord"
        ARBITER = "arbiter", "Arbiter"

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name="invites")
    role = models.CharField(max_length=10, choices=Role.choices)
    token = models.CharField(max_length=64, unique=True, default=new_invite_token)
    accepted_by = models.ForeignKey(Party, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    accepted_at = models.DateTimeField(null=True, blank=True)
    declined_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class SettlementProposal(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        EXECUTED = "EXECUTED", "Executed"
        SUPERSEDED = "SUPERSEDED", "Superseded"
        EXPIRED = "EXPIRED", "Expired"

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name="proposals")
    tenant_amount = models.DecimalField(max_digits=20, decimal_places=6)
    landlord_amount = models.DecimalField(max_digits=20, decimal_places=6)
    reason = models.CharField(max_length=500, blank=True)
    proposed_by = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="proposals")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    prepared_txs = models.JSONField(default=list, help_text="Unsigned payout transactions, in submit order.")
    signatures = models.JSONField(default=dict, help_text="{party_id: [signed tx JSON or null, one per prepared tx]}")
    executed_hashes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Proposal {self.pk} ({self.status})"


class Evidence(models.Model):
    class Kind(models.TextChoices):
        MOVE_IN = "move_in", "Move-in"
        MOVE_OUT = "move_out", "Move-out"
        DAMAGE = "damage", "Damage"

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name="evidence")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    file = models.FileField(upload_to="evidence/%Y%m%d/")
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="evidence")
    anchor_tx_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "evidence"


class LedgerEvent(models.Model):
    """One line of a lease's timeline. Every on-ledger action writes one, with its transaction hash."""

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=32)
    tx_hash = models.CharField(max_length=64, blank=True)
    summary = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.summary


class XamanRequest(models.Model):
    """One Xaman sign request. The browser only sees our id, the QR code and the deep link."""

    class Purpose(models.TextChoices):
        SIGNIN = "signin", "Sign in"
        ESCROW_CREATE = "escrow_create", "Lock the deposit"
        PAYOUT_SIGN = "payout_sign", "Approve a payout"
        EVIDENCE_ANCHOR = "evidence_anchor", "Anchor a photo"
        TRUSTSET = "trustset", "Add a trust line"

    class Status(models.TextChoices):
        OPEN = "open", "Waiting for Xaman"
        SIGNED = "signed", "Signed, finishing"
        FINISHING = "finishing", "Finishing"
        DONE = "done", "Done"
        REJECTED = "rejected", "Rejected in Xaman"
        EXPIRED = "expired", "Expired"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    xaman_uuid = models.CharField(max_length=64, unique=True)
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    party = models.ForeignKey(Party, on_delete=models.CASCADE, null=True, blank=True, related_name="xaman_requests")
    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, null=True, blank=True)
    proposal = models.ForeignKey(SettlementProposal, on_delete=models.CASCADE, null=True, blank=True)
    evidence = models.ForeignKey(Evidence, on_delete=models.CASCADE, null=True, blank=True)
    payload_index = models.PositiveIntegerField(null=True, blank=True, help_text="Which prepared payout (payout_sign).")
    currency_code = models.CharField(max_length=3, blank=True, help_text="Trust line currency (trustset).")
    deeplink = models.URLField(max_length=300, blank=True)
    pushed = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    error = models.CharField(max_length=300, blank=True)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    signed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class FaucetGrant(models.Model):
    """One use of the Testnet faucet, for the once-per-24-hours limit."""

    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="faucet_grants")
    kind = models.CharField(max_length=16, help_text="activate | tokens:EUR | tokens:USD")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
