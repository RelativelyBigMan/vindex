"""Models: platform wallets, demo personas, test currencies, leases and their timeline.

Seeds are stored in the database ONLY because this app runs on XRPL Testnet/Devnet
(ledger/client.py refuses to start against mainnet).
"""
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
        CREDENTIAL_ISSUER = "credential_issuer", "Credential issuer"

    role = models.CharField(max_length=32, choices=Role.choices, unique=True)
    address = models.CharField(max_length=64, unique=True)
    seed = models.CharField(max_length=64)

    def __str__(self):
        return f"{self.get_role_display()} ({self.address})"


class Party(models.Model):
    class Role(models.TextChoices):
        TENANT = "tenant", "Tenant"
        LANDLORD = "landlord", "Landlord"
        ARBITER = "arbiter", "Arbiter"

    class WalletType(models.TextChoices):
        DEMO = "demo", "Demo (server-held Testnet key)"
        GEMWALLET = "gemwallet", "GemWallet"
        CROSSMARK = "crossmark", "Crossmark"

    display_name = models.CharField(max_length=120)
    role = models.CharField(max_length=16, choices=Role.choices)
    address = models.CharField(max_length=64, unique=True)
    seed = models.CharField(max_length=64, null=True, blank=True, help_text="Null for external wallets.")
    wallet_type = models.CharField(max_length=16, choices=WalletType.choices, default=WalletType.DEMO)

    class Meta:
        verbose_name_plural = "parties"

    def __str__(self):
        return f"{self.display_name} ({self.get_role_display()})"


class Currency(models.Model):
    code = models.CharField(max_length=3, unique=True)
    label = models.CharField(max_length=32)
    issuer_address = models.CharField(max_length=64)

    class Meta:
        verbose_name_plural = "currencies"

    def __str__(self):
        return self.label


# --------------------------------------------------------------------------------------
# Patch 3: leases, settlement proposals, evidence and the ledger timeline
# --------------------------------------------------------------------------------------

class Lease(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Being set up"
        SETTLEMENT_READY = "SETTLEMENT_READY", "Ready to lock"
        ACTIVE = "ACTIVE", "Locked"
        RETURNED = "RETURNED", "Returned"
        IN_SETTLEMENT = "IN_SETTLEMENT", "In settlement"
        SETTLED = "SETTLED", "Settled"
        FAILED = "FAILED", "Failed"

    class ReleaseReason(models.TextChoices):
        EARLY_MOVE_OUT = "early_move_out", "Early move-out"
        DAMAGE_CLAIM = "damage_claim", "Damage claim"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="leases_as_tenant")
    landlord = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="leases_as_landlord")
    arbiter = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="leases_as_arbiter")
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=20, decimal_places=6)
    end_at = models.DateTimeField()
    grace_minutes = models.PositiveIntegerField(default=1)
    cancel_after = models.DateTimeField(help_text="end_at + grace: from then on the deposit returns to the tenant.")

    settlement_address = models.CharField(max_length=64, blank=True)
    settlement_seed = models.CharField(max_length=64, blank=True, help_text="Testnet only; the master key is disabled.")
    condition_hex = models.CharField(max_length=128, blank=True)
    fulfillment_hex = models.CharField(max_length=128, blank=True, help_text="SECRET: releases the escrow.")
    escrow_owner = models.CharField(max_length=64, blank=True)
    escrow_sequence = models.BigIntegerField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    setup_step = models.CharField(max_length=20, blank=True)
    release_reason = models.CharField(max_length=20, choices=ReleaseReason.choices, blank=True)
    error = models.TextField(blank=True)
    archived = models.BooleanField(default=False, help_text="Hidden from lists (demo_reset). The ledger is untouched.")
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
        """The three parties whose signatures count, in a fixed order."""
        return [self.tenant, self.landlord, self.arbiter]

    def role_of(self, party):
        """'tenant' | 'landlord' | 'arbiter' for a party of this lease, else None."""
        if party is None:
            return None
        for role in ("tenant", "landlord", "arbiter"):
            if getattr(self, f"{role}_id") == party.pk:
                return role
        return None


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
    signatures = models.JSONField(default=dict, help_text="{party_id: [signed tx JSON per prepared tx]}")
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
