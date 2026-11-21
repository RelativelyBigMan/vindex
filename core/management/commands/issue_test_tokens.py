"""Send test EUR or USD from the platform's issuer to an account (Testnet only, admin use).

    manage.py issue_test_tokens rADDRESS EUR [amount]     (default amount 5000)

The account needs a trust line for the currency first (Vindex: My deposits → Test funds).
"""
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from xrpl.core.addresscodec import is_valid_classic_address
from xrpl.wallet import Wallet

from core.models import Currency, PlatformWallet
from ledger import services
from ledger.client import explorer_tx
from ledger.errors import LedgerError


class Command(BaseCommand):
    help = "Send test EUR or USD from the platform issuer to an account (needs its trust line)."

    def add_arguments(self, parser):
        parser.add_argument("address")
        parser.add_argument("currency", help="EUR or USD")
        parser.add_argument("amount", nargs="?", default="5000")

    def handle(self, address, currency, amount, **options):
        if not is_valid_classic_address(address):
            raise CommandError(f"{address!r} is not a valid XRP Ledger address.")
        found = Currency.objects.filter(code=currency.upper()).first()
        if found is None:
            raise CommandError(f"Unknown currency {currency!r}. Known: {', '.join(Currency.objects.values_list('code', flat=True)) or 'none (run bootstrap)'}.")
        try:
            value = Decimal(amount)
        except InvalidOperation as exc:
            raise CommandError(f"{amount!r} is not a number.") from exc
        if not value.is_finite() or value <= 0:
            raise CommandError("The amount must be greater than zero.")
        issuer = PlatformWallet.objects.filter(address=found.issuer_address).first()
        if issuer is None:
            raise CommandError("This server doesn't hold that token's issuer. Run bootstrap first.")
        try:
            if not services.has_trust_line(address, found.code, found.issuer_address):
                raise CommandError(f"{address} has no {found.code} trust line yet. Add it in the app first (My deposits → Test funds).")
            tx_hash = services.issue_tokens(Wallet.from_seed(issuer.seed), address, found.code, value)
        except LedgerError as exc:
            raise CommandError(f"Ledger error ({exc.engine_result}): {exc.message}") from exc
        cache.delete(f"balances:{address}")
        self.stdout.write(self.style.SUCCESS(f"Sent {value} {found.code} to {address}: {tx_hash}"))
        self.stdout.write(explorer_tx(tx_hash))
