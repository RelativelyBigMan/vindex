"""Stage three fresh leases for the pitch (A normal end, B early move-out, C damage dispute) and print their URLs.

    A  escrow locked, automatic return due 4 minutes after creation (created last, so about 3.5 minutes remain)
    B  escrow locked on a long lease; the release is triggered live
    C  already in settlement: the landlord's 850 / 150 split is signed by the landlord, waiting for the arbiter

B's and C's settlement accounts are created in parallel (different accounts), so the whole run stays
under 3 minutes. Everything goes through core.lifecycle, exactly like the web app.
"""
import threading
import time
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from core import lifecycle
from core.lifecycle import InvalidInput, InvalidTransition
from core.models import Currency, Lease, Party, PlatformWallet
from ledger import services
from ledger.client import network
from ledger.errors import LedgerError

AMOUNT = Decimal("1000")
PLANS = {
    "A": ("Via Sparano 12 (normal end)", timedelta(minutes=3), 1),
    "B": ("Corso Cavour 48 (early move-out)", timedelta(days=30), 60),
    "C": ("Piazza Mercantile 7 (damage dispute)", timedelta(days=30), 60),
}
RETRIES = 3


class Command(BaseCommand):
    help = "Create three staged EUR leases of 1,000 for the live demo and print their URLs."

    def add_arguments(self, parser):
        parser.add_argument("--base", default="http://127.0.0.1:8000", help="Site address used in the printed URLs.")
        parser.add_argument("--currency", default="EUR", choices=["EUR", "USD"])
        parser.add_argument("--keep", action="store_true", help="Keep earlier staged leases in the lists.")
        parser.add_argument("--a-minutes", type=int, default=4, help="Scenario A returns this many minutes after it is created.")

    def say(self, letter, text):
        self.stdout.write(f"  [{letter}] {text}")
        self.stdout.flush()

    def retry(self, letter, what, fn):
        """Run one ledger step, retrying on network or faucet hiccups with a clear line each time."""
        for attempt in range(1, RETRIES + 1):
            try:
                return fn()
            except (LedgerError, InvalidTransition, InvalidInput) as exc:
                code = getattr(exc, "engine_result", None) or getattr(exc, "code", "")
                if attempt == RETRIES or code not in ("network_error", "faucet_unavailable", "busy", "terPRE_SEQ", "unknown"):
                    raise CommandError(f"Scenario {letter}: {what} failed: {exc.message}") from exc
                self.say(letter, f"{what}: {exc.message} Retrying in {5 * attempt}s ({attempt}/{RETRIES - 1})")
                time.sleep(5 * attempt)

    def handle(self, *args, **options):
        started = time.monotonic()
        demo = Party.WalletType.DEMO
        tenant = Party.objects.filter(role="tenant", wallet_type=demo).order_by("pk").first()
        landlord = Party.objects.filter(role="landlord", wallet_type=demo).order_by("pk").first()
        arbiter = Party.objects.filter(role="arbiter", wallet_type=demo).order_by("pk").first()
        currency = Currency.objects.filter(code=options["currency"]).first()
        if not all((tenant, landlord, arbiter, currency)) or not PlatformWallet.objects.filter(role="treasury").exists():
            raise CommandError("The demo wallets are missing. Run: python manage.py bootstrap_demo")
        self.stdout.write(f"Network: {network()['name']}. Staging three leases of {AMOUNT} {currency.code}...")

        # The tenant locks 3 deposits; top the wallet up first so a rehearsal never runs dry.
        needed = AMOUNT * 3
        held = self.retry("-", "reading the tenant's balance",
                          lambda: services.get_token_balance(tenant.address, currency.code, currency.issuer_address))
        if held < needed:
            issuer_role = f"issuer_{currency.code.lower()}"
            issuer = lifecycle.platform_wallet(issuer_role)
            self.retry("-", "topping up the tenant", lambda: services.issue_tokens(issuer, tenant.address, currency.code, needed - held))
            self.stdout.write(f"  tenant topped up with {needed - held} {currency.code}")

        if not options["keep"]:
            Lease.objects.filter(archived=False).exclude(scenario="").update(scenario="", archived=True)

        leases = {}

        def create(letter):
            title, length, grace = PLANS[letter]
            if letter == "A":
                length = timedelta(minutes=max(options["a_minutes"] - grace, 1))
            leases[letter] = Lease.objects.create(
                tenant=tenant, landlord=landlord, arbiter=arbiter, currency=currency, amount=AMOUNT, title=title,
                scenario=letter, end_at=timezone.now() + length, grace_minutes=grace,
            )

        def setup(letter, errors):
            try:
                lease = leases[letter]
                Lease.objects.filter(pk=lease.pk).update(setup_step="starting")
                self.retry(letter, "settlement account", lambda: self._setup(lease))
                self.say(letter, "settlement account ready (2 of 3, own key disabled)")
            except CommandError as exc:
                errors[letter] = str(exc)
            finally:
                connection.close()

        def lock(letter):
            lease = leases[letter]
            lease.refresh_from_db()
            self.retry(letter, "locking the deposit", lambda: lifecycle.create_escrow_demo(lease))
            self.say(letter, f"deposit locked in escrow, automatic return {timezone.localtime(lease.cancel_after):%d %b %H:%M:%S}")

        # B and C first. Their settlement accounts are different accounts, so they are set up at the same time.
        errors = {}
        create("B")
        create("C")
        threads = [threading.Thread(target=setup, args=(letter, errors)) for letter in "BC"]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if errors:
            raise CommandError("; ".join(errors.values()))
        lock("B")
        lock("C")
        c = leases["C"]
        self.retry("C", "damage claim", lambda: lifecycle.release(c, landlord, Lease.ReleaseReason.DAMAGE_CLAIM)
                   if Lease.objects.get(pk=c.pk).status == Lease.Status.ACTIVE else None)
        self.retry("C", "landlord's proposal", lambda: lifecycle.propose(
            c, landlord, "850", "150", "Scratched parquet in the living room; repair quote of 150.")
            if lifecycle.open_proposal(c) is None else None)
        self.say("C", "landlord claimed damage and proposed 850 / 150 (1 of 2 approvals)")

        # A last: its clock starts when it is created, so nothing else may delay it.
        create("A")
        setup("A", errors)
        if errors:
            raise CommandError("; ".join(errors.values()))
        lock("A")
        leases = {letter: leases[letter] for letter in "ABC"}

        base = options["base"].rstrip("/")
        took = time.monotonic() - started
        self.stdout.write("")
        for letter, lease in leases.items():
            lease.refresh_from_db()
            self.stdout.write(f"  {letter}  {lease.get_status_display():<14} {base}/leases/{lease.pk}/")
        left = int((leases["A"].cancel_after - timezone.now()).total_seconds())
        self.stdout.write(self.style.SUCCESS(
            f"\nReady in {took:.0f} s. Presenter view: {base}/demo/stage/\n"
            f"A returns automatically in about {max(left, 0) // 60} min {max(left, 0) % 60} s "
            "(keep `python manage.py run_bot` running, or use Run bot now)."
        ))

    def _setup(self, lease):
        lifecycle._run_setup(lease.pk)
        lease.refresh_from_db()
        if lease.status == Lease.Status.FAILED:
            # Start clean for the retry: the failed attempt left nothing the lease depends on.
            message = lease.error
            Lease.objects.filter(pk=lease.pk).update(status=Lease.Status.DRAFT, error="", setup_step="starting")
            raise LedgerError("network_error", message)
