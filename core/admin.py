from django.contrib import admin

from .models import Currency, Party, PlatformWallet


@admin.register(PlatformWallet)
class PlatformWalletAdmin(admin.ModelAdmin):
    list_display = ("role", "address")
    exclude = ("seed",)  # never show keys in the admin, even Testnet ones
    readonly_fields = ("role", "address")


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ("display_name", "role", "wallet_type", "address")
    list_filter = ("role", "wallet_type")
    search_fields = ("display_name", "address")
    exclude = ("seed",)


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "issuer_address")
