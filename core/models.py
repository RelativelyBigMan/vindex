"""Patch 1 models: platform wallets, demo personas and test currencies.

Seeds are stored in the database ONLY because this app runs on XRPL Testnet/Devnet
(ledger/client.py refuses to start against mainnet). Lease and related models come in Patch 3.
"""
from django.db import models


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
