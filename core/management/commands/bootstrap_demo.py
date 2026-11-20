"""Create (or reuse) every platform wallet, the demo personas and the test tokens.

Idempotent: wallets already in the database are reused, and every ledger step
first checks the ledger and is skipped when already done.
"""
import time
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from xrpl.wallet import Wallet, generate_faucet_wallet

from core.models import Currency, Party, PlatformWallet
from ledger import services
from ledger.client import explorer_account, get_client, network
from ledger.errors import LedgerError

FUND_XRP = Decimal("25")
TREASURY_KEEP = Decimal("12")  # left in the treasury after each funding (reserve plus fees)

PLATFORM_ROLES = ["bot", "credential_issuer", "issuer_eur", "issuer_usd"]
PERSONAS = [
    ("tenant", "Giulia Rossi"),
    ("landlord", "Marco Bianchi"),
    ("arbiter", "Bari Housing Mediation"),
]
CURRENCIES = [("EUR", "EUR (test)", "issuer_eur"), ("USD", "USD (test)", "issuer_usd")]
TOKEN_GRANTS = {"tenant": Decimal("5000"), "landlord": Decimal("1000")}
ARBITER_CREDENTIAL = "VERIFIED_ARBITER"
ARBITER_CREDENTIAL_URI = "safedeposit:mediation-registry"  # short and free of personal data: it is public forever


class Command(BaseCommand):
    help = "Create the demo wallets, personas and test tokens on the XRPL test network (safe to rerun)."

    def handle(self, *args, **options):
        self.client = get_client()
        self.created = 0
        net = network()
        self.stdout.write(f"Network: {net['name']} ({net['json_rpc']})")

        try:
            treasury = self.get_treasury()
            wallets = {role: self.platform_wallet(role, treasury) for role in PLATFORM_ROLES}
            parties = {role: self.persona(role, name, treasury) for role, name in PERSONAS}

            # Issuer flags BEFORE any trust line exists.
            for role in ("issuer_eur", "issuer_usd"):
                hashes = services.set_issuer_flags(wallets[role])
                self.step(f"{role}: DefaultRipple + Allow Trust Line Locking", "set" if hashes else "already set")

            for code, label, issuer_role in CURRENCIES:
                Currency.objects.update_or_create(
                    code=code, defaults={"label": label, "issuer_address": wallets[issuer_role].address}
                )

            for code, _label, issuer_role in CURRENCIES:
                issuer = wallets[issuer_role]
                for role, wallet in parties.items():
                    tx = services.ensure_trust_line(wallet, code, issuer.address)
                    self.step(f"{role}: trust line {code}", "created" if tx else "exists")
                for role, target in TOKEN_GRANTS.items():
                    have = services.get_balances(parties[role].address, self.client).get(code, Decimal("0"))
                    if have < target:
                        services.issue_tokens(issuer, parties[role].address, code, target - have)
                        self.step(f"{role}: issued {target - have} {code}", "done")
                    else:
                        self.step(f"{role}: holds {have} {code}", "enough")
            self.arbiter_credential(wallets["credential_issuer"], parties["arbiter"])
        except LedgerError as exc:
            raise CommandError(f"Ledger error ({exc.engine_result}): {exc.message}") from exc

        self.summary(treasury, wallets, parties)
        self.stdout.write(self.style.SUCCESS(f"\nDone. New wallets created in this run: {self.created}"))

    # ---- helpers ----
    def step(self, text, state):
        self.stdout.write(f"  {text}: {state}")

    def arbiter_credential(self, registry, arbiter):
        """The mediation registry vouches for the arbiter with an XRPL Credential; the arbiter accepts it."""
        def held():
            return next(
                (c for c in services.get_credentials(arbiter.address, self.client)
                 if c["issuer"] == registry.address and c["type"] == ARBITER_CREDENTIAL), None,
            )

        try:
            credential = held()
            if credential is None:
                services.issue_credential(registry, arbiter.address, ARBITER_CREDENTIAL, ARBITER_CREDENTIAL_URI)
                credential = held()
                self.step("arbiter credential", "issued by the registry")
            if credential and not credential["accepted"]:
                services.accept_credential(arbiter, registry.address, ARBITER_CREDENTIAL)
                self.step("arbiter credential", "accepted by the arbiter")
            else:
                self.step("arbiter credential", "already accepted")
        except LedgerError as exc:
            if exc.engine_result != "temDISABLED":
                raise
            self.stdout.write(self.style.WARNING("  Credentials are disabled on this network; the arbiter shows as unverified."))

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
                        "wait a few minutes and rerun bootstrap_demo (existing wallets are reused)."
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

    def persona(self, role, name, treasury):
        row = Party.objects.filter(role=role, wallet_type="demo", display_name=name).first()
        if row:
            wallet = Wallet.from_seed(row.seed)
        else:
            wallet = Wallet.create()
            Party.objects.create(display_name=name, role=role, address=wallet.address, seed=wallet.seed, wallet_type="demo")
            self.created += 1
        funded = self.ensure_funded(wallet, treasury)
        self.step(f"{role} persona '{name}'", ("created" if not row else "reused") + (", funded" if funded else ""))
        return wallet

    def summary(self, treasury, wallets, parties):
        names = dict(PERSONAS)
        rows = [("treasury", "-", treasury.address)]
        rows += [(role, "-", w.address) for role, w in wallets.items()]
        rows += [(role, names[role], w.address) for role, w in parties.items()]
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
