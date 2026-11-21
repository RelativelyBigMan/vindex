"""Create (or reuse) the platform wallets (treasury, bot, token issuers) and the test tokens. Idempotent."""
import time
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from xrpl.wallet import Wallet, generate_faucet_wallet

from core.models import Currency, PlatformWallet
from ledger import services
from ledger.client import explorer_account, get_client, network
from ledger.errors import LedgerError

FUND_XRP = Decimal("25")
TREASURY_KEEP = Decimal("12")  # left in the treasury after each funding (reserve plus fees)

PLATFORM_ROLES = ["bot", "issuer_eur", "issuer_usd"]
CURRENCIES = [("EUR", "EUR (test)", "issuer_eur"), ("USD", "USD (test)", "issuer_usd")]


class Command(BaseCommand):
    help = "Create the platform wallets and test tokens on the XRPL test network (safe to rerun)."

    def handle(self, *args, **options):
        self.client = get_client()
        self.created = 0
        net = network()
        self.stdout.write(f"Network: {net['name']} ({net['json_rpc']})")

        try:
            treasury = self.get_treasury()
            wallets = {role: self.platform_wallet(role, treasury) for role in PLATFORM_ROLES}

            # Issuer flags BEFORE any trust line exists.
            for role in ("issuer_eur", "issuer_usd"):
                hashes = services.set_issuer_flags(wallets[role])
                self.step(f"{role}: DefaultRipple + Allow Trust Line Locking", "set" if hashes else "already set")

            for code, label, issuer_role in CURRENCIES:
                Currency.objects.update_or_create(
                    code=code, defaults={"label": label, "issuer_address": wallets[issuer_role].address}
                )
        except LedgerError as exc:
            raise CommandError(f"Ledger error ({exc.engine_result}): {exc.message}") from exc

        self.summary(treasury, wallets)
        self.stdout.write(self.style.SUCCESS(f"\nDone. New wallets created in this run: {self.created}"))

    # ---- helpers ----
    def step(self, text, state):
        self.stdout.write(f"  {text}: {state}")

    def faucet(self, wallet=None):
        """Ask the faucet for XRP (new wallet, or top up `wallet`), retrying with backoff when rate-limited."""
        delay = 5
        for attempt in range(1, 7):
            try:
                return generate_faucet_wallet(self.client, wallet=wallet)
            except Exception as exc:  # the faucet answers rate limits and outages with assorted errors
                if attempt == 6:
                    raise CommandError(
                        f"The faucet keeps failing ({exc}). It is probably rate-limiting this IP: "
                        "wait a few minutes and rerun bootstrap (existing wallets are reused)."
                    ) from exc
                self.stdout.write(self.style.WARNING(f"  faucet busy ({str(exc)[:80]}); retrying in {delay}s"))
                time.sleep(delay)
                delay *= 2

    def get_treasury(self):
        row = PlatformWallet.objects.filter(role="treasury").first()
        if row:
            wallet = Wallet.from_seed(row.seed)
            if not services.account_exists(wallet.address, self.client):
                self.stdout.write("  treasury is not on this network (reset?); funding it again")
                self.faucet(wallet)
            self.step("treasury", "reused")
            return wallet
        self.stdout.write("  creating the treasury from the faucet...")
        wallet = self.faucet()
        PlatformWallet.objects.create(role="treasury", address=wallet.address, seed=wallet.seed)
        self.created += 1
        self.step("treasury", "created")
        return wallet

    def ensure_treasury(self, treasury, needed):
        """Top the treasury up from the faucet until it can pay `needed` XRP and keep its reserve."""
        for _ in range(6):
            balance = services.get_balances(treasury.address, self.client)["XRP"]
            if balance >= needed + TREASURY_KEEP:
                return
            self.stdout.write(f"  treasury has {balance} XRP; topping up from the faucet")
            self.faucet(treasury)
        raise CommandError("Couldn't top the treasury up from the faucet. Wait a few minutes and rerun.")

    def ensure_funded(self, wallet, treasury):
        if services.account_exists(wallet.address, self.client):
            return False
        self.ensure_treasury(treasury, FUND_XRP)
        services.fund_xrp(treasury, wallet.address, FUND_XRP)
        return True

    def platform_wallet(self, role, treasury):
        row = PlatformWallet.objects.filter(role=role).first()
        if row:
            wallet = Wallet.from_seed(row.seed)
        else:
            wallet = Wallet.create()
            PlatformWallet.objects.create(role=role, address=wallet.address, seed=wallet.seed)
            self.created += 1
        funded = self.ensure_funded(wallet, treasury)
        self.step(role, ("created" if not row else "reused") + (", funded" if funded else ""))
        return wallet

    def summary(self, treasury, wallets):
        rows = [("treasury", "-", treasury.address)]
        rows += [(role, "-", w.address) for role, w in wallets.items()]
        self.stdout.write("\n" + f"{'ROLE':<18} {'NAME':<24} {'ADDRESS':<36} {'XRP':>8} {'EUR':>7} {'USD':>7}  EXPLORER")
        for role, name, address in rows:
            b = services.get_balances(address, self.client)
            fmt = lambda code: f"{b.get(code, Decimal('0')):.0f}" if code in b else "-"
            self.stdout.write(
                f"{role:<18} {name:<24} {address:<36} {b['XRP']:>8.2f} {fmt('EUR'):>7} {fmt('USD'):>7}  {explorer_account(address)}"
            )
        self.stdout.write("\nIssuer flags:")
        for role in ("issuer_eur", "issuer_usd"):
            f = services.get_account_flags(wallets[role].address, self.client)
            self.stdout.write(
                f"  {role}: DefaultRipple={'yes' if f['default_ripple'] else 'NO'}, "
                f"AllowTrustLineLocking={'yes' if f['allow_trustline_locking'] else 'NO'}"
            )
