"""Testnet-only faucet: activation XRP for a new account, once per account per 24 hours.

Test tokens are handed out by an admin: `manage.py issue_test_tokens <address> <EUR|USD> [amount]`.
"""
import threading
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone

from ledger import services

from . import lifecycle
from .lifecycle import InvalidTransition
from .models import Currency, FaucetGrant

ACTIVATE_XRP = Decimal("50")
COOLDOWN = timedelta(hours=24)
TREASURY_KEEP_XRP = Decimal("3")
_guard = threading.Lock()


def _used_recently(party, kind):
    return FaucetGrant.objects.filter(party=party, kind=kind, created_at__gte=timezone.now() - COOLDOWN).exists()


def _require_fresh(party, kind):
    if _used_recently(party, kind):
        raise InvalidTransition("You can use this once every 24 hours.", "too_soon")


def status(party):
    activated = services.account_exists(party.address)
    balances = {code: lifecycle.plain(value) for code, value in services.get_balances(party.address).items()} if activated else {}
    currencies = list(Currency.objects.order_by("code"))
    lines = {c.code: activated and services.has_trust_line(party.address, c.code, c.issuer_address) for c in currencies}
    available = {"activate": not activated and not _used_recently(party, "activate")}
    return {"activated": activated, "balances": balances, "trust_lines": lines, "available": available}


def activate(party):
    with _guard:
        _require_fresh(party, "activate")
        if services.account_exists(party.address):
            raise InvalidTransition("Your account is already active on the ledger.", "already_active")
        treasury = lifecycle.platform_wallet("treasury")
        if services.get_balances(treasury.address)["XRP"] < ACTIVATE_XRP + TREASURY_KEEP_XRP:
            services.faucet_top_up(treasury)
        tx_hash = services.fund_xrp(treasury, party.address, ACTIVATE_XRP)
        FaucetGrant.objects.create(party=party, kind="activate")
    cache.delete(f"balances:{party.address}")
    return tx_hash

