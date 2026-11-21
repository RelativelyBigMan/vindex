from django.contrib import admin

from .models import Currency, Evidence, FaucetGrant, Invite, LedgerEvent, Lease, Party, PlatformWallet, SettlementProposal, XamanRequest


@admin.register(PlatformWallet)
class PlatformWalletAdmin(admin.ModelAdmin):
    list_display = ("role", "address")
    exclude = ("seed",)  # never show keys in the admin, even Testnet ones
    readonly_fields = ("role", "address")


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ("display_name", "address", "created_at")
    search_fields = ("display_name", "address")
    exclude = ("xaman_user_token",)


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "issuer_address")


@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "setup_step", "amount", "currency", "tenant", "landlord", "arbiter", "cancel_after", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("id", "settlement_address", "escrow_owner")
    # Secrets stay out of the admin; ledger facts are read-only so they can't drift from the ledger.
    exclude = ("settlement_seed", "fulfillment_hex")
    readonly_fields = (
        "end_at", "grace_minutes", "cancel_after", "settlement_address", "condition_hex",
        "escrow_owner", "escrow_sequence", "created_at", "updated_at",
    )


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    list_display = ("lease", "role", "accepted_by", "accepted_at", "declined_at", "revoked")
    exclude = ("token",)


@admin.register(SettlementProposal)
class SettlementProposalAdmin(admin.ModelAdmin):
    list_display = ("id", "lease", "status", "tenant_amount", "landlord_amount", "proposed_by", "created_at")
    list_filter = ("status",)
    readonly_fields = ("prepared_txs", "signatures", "executed_hashes")


@admin.register(Evidence)
class EvidenceAdmin(admin.ModelAdmin):
    list_display = ("id", "lease", "kind", "uploaded_by", "sha256", "anchor_tx_hash", "created_at")
    list_filter = ("kind",)


@admin.register(LedgerEvent)
class LedgerEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "lease", "kind", "summary", "tx_hash")
    list_filter = ("kind",)
    search_fields = ("summary", "tx_hash")


@admin.register(XamanRequest)
class XamanRequestAdmin(admin.ModelAdmin):
    list_display = ("created_at", "purpose", "status", "party", "lease")
    list_filter = ("purpose", "status")
    readonly_fields = ("result",)


@admin.register(FaucetGrant)
class FaucetGrantAdmin(admin.ModelAdmin):
    list_display = ("created_at", "party", "kind")
