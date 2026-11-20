"""Prove the network is usable for the demo: server reachable, issuers configured, token escrow works."""
import time
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.models.transactions import EscrowCancel, EscrowCreate
from xrpl.utils import datetime_to_ripple_time
from xrpl.wallet import Wallet

from core.models import Currency, Party, PlatformWallet
from ledger import services
from ledger.client import explorer_tx, get_client, network
from ledger.conditions import make_condition
from ledger.errors import LedgerError

DISABLED_HINT = "Token escrow is disabled on this network; set XRPL_NETWORK=devnet and rerun bootstrap_demo."


class Command(BaseCommand):
    help = "Check the XRPL test network and run a 1 EUR token-escrow round trip."

    def handle(self, *args, **options):
        self.failed = False
        client = get_client()

        # 1. Server reachable.
        try:
            index = services.latest_validated_ledger_index(client)
            self.ok(f"Server reachable: {network()['name']}, latest validated ledger {index}")
        except LedgerError as exc:
            self.fail(f"Server not reachable: {exc.message}")
            return self.finish()

        # 2. Issuer flags.
        for role in ("issuer_eur", "issuer_usd"):
            row = PlatformWallet.objects.filter(role=role).first()
            if not row:
                self.fail(f"{role}: missing. Run bootstrap_demo first.")
                continue
            flags = services.get_account_flags(row.address, client)
            if flags["default_ripple"] and flags["allow_trustline_locking"]:
                self.ok(f"{role}: DefaultRipple and Allow Trust Line Locking are set")
            else:
                self.fail(f"{role}: flags missing {flags}. Rerun bootstrap_demo.")

        # 3. Token escrow round trip.
        tenant = Party.objects.filter(role="tenant", wallet_type="demo").first()
        landlord = Party.objects.filter(role="landlord", wallet_type="demo").first()
        eur = Currency.objects.filter(code="EUR").first()
        if not (tenant and landlord and eur):
            self.fail("Demo personas or the EUR currency are missing. Run bootstrap_demo first.")
            return self.finish()
        try:
            self.escrow_round_trip(client, Wallet.from_seed(tenant.seed), landlord.address, eur)
        except LedgerError as exc:
            if exc.engine_result == "temDISABLED":
                self.fail(DISABLED_HINT)
            else:
                self.fail(f"Token escrow test failed ({exc.engine_result}): {exc.message}")
        return self.finish()

    def escrow_round_trip(self, client, tenant, landlord_address, eur):
        before = services.get_balances(tenant.address, client).get("EUR", Decimal("0"))
        condition_hex, _fulfillment = make_condition()
        cancel_after = services.ledger_close_time(client) + timedelta(seconds=30)
        created = services.submit(
            EscrowCreate(
                account=tenant.address,
                destination=landlord_address,
                amount=IssuedCurrencyAmount(currency=eur.code, issuer=eur.issuer_address, value="1"),
                condition=condition_hex,
                cancel_after=datetime_to_ripple_time(cancel_after),
            ),
            tenant,
            client,
        )
        sequence = created["tx_json"]["Sequence"]
        self.ok(f"EscrowCreate 1 EUR (sequence {sequence}): {explorer_tx(created['hash'])}")
        during = services.get_balances(tenant.address, client).get("EUR", Decimal("0"))
        if during != before - 1:
            self.fail(f"Tenant balance should have dropped by 1 EUR while locked (before {before}, now {during})")

        self.stdout.write("  waiting for the ledger close time to pass CancelAfter + 10 s", ending="")
        self.stdout.flush()
        deadline = cancel_after + timedelta(seconds=10)
        while services.ledger_close_time(client) <= deadline:
            self.stdout.write(".", ending="")
            self.stdout.flush()
            time.sleep(4)
        self.stdout.write("")

        cancelled = services.submit(
            EscrowCancel(account=tenant.address, owner=tenant.address, offer_sequence=sequence), tenant, client
        )
        self.ok(f"EscrowCancel: {explorer_tx(cancelled['hash'])}")
        after = services.get_balances(tenant.address, client).get("EUR", Decimal("0"))
        if after == before:
            self.ok(f"Tenant balance restored ({after} EUR)")
        else:
            self.fail(f"Tenant balance not restored: before {before}, after {after}")

    def ok(self, text):
        self.stdout.write(self.style.SUCCESS("PASS ") + text)

    def fail(self, text):
        self.failed = True
        self.stdout.write(self.style.ERROR("FAIL ") + text)

    def finish(self):
        if self.failed:
            self.stdout.write(self.style.ERROR("\nPreflight FAILED."))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("\nPreflight passed: token escrow works on this network."))
