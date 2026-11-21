"""Run the three demo scenarios on the XRPL test network, with no UI, and verify each step on the ledger.

A  normal end:      settlement account -> escrow -> wait past CancelAfter -> the bot cancels -> tenant has it back
B  early move-out:  settlement account -> escrow -> tenant releases -> 1,000 back to the tenant (tenant + landlord sign)
C  damage dispute:  settlement account -> escrow -> landlord releases -> 850 / 150 (landlord + arbiter sign)

Scenario A is started first; B and C run while A waits for its deadline.
Each run costs the tenant 150 tokens (scenario C); bootstrap_demo tops the tenant back up.
"""
import time
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from xrpl.models.transactions import Payment
from xrpl.wallet import Wallet, generate_faucet_wallet

from core.models import Currency, Party, PlatformWallet
from ledger import services
from ledger.client import explorer_account, explorer_tx, network
from ledger.conditions import make_condition
from ledger.errors import LedgerError

DEPOSIT = Decimal("1000")
LEASE = timedelta(minutes=1)
GRACE = timedelta(minutes=1)
TREASURY_KEEP = Decimal("3")


class CheckFailed(Exception):
    pass


class Command(BaseCommand):
    help = "Prove the deposit mechanics on the XRPL test network: scenarios A, B and C plus negative checks."

    def add_arguments(self, parser):
        parser.add_argument("--currency", choices=["EUR", "USD"], default="EUR")
        parser.add_argument("--scenario", choices=["A", "B", "C", "all"], default="all")

    # ---- output helpers ----
    def say(self, tag, text, tx=None):
        link = f"\n      {explorer_tx(tx)}" if tx else ""
        self.stdout.write(f"[{tag}] {text}{link}")

    def expect(self, tag, condition, text):
        if not condition:
            raise CheckFailed(text)
        self.stdout.write(f"[{tag}] " + self.style.SUCCESS("ok ") + text)

    def token(self, address):
        return services.get_token_balance(address, self.code, self.issuer)

    # ---- setup ----
    def handle(self, *args, **options):
        self.code = options["currency"]
        wanted = ["A", "B", "C"] if options["scenario"] == "all" else [options["scenario"]]
        try:
            currency = Currency.objects.get(code=self.code)
            self.issuer = currency.issuer_address
            self.parties = {r: Party.objects.filter(role=r, is_persona=True).order_by("pk")[0] for r in ("tenant", "landlord", "arbiter")}
            self.wallets = {r: Wallet.from_seed(p.seed) for r, p in self.parties.items()}
            self.treasury = Wallet.from_seed(PlatformWallet.objects.get(role="treasury").seed)
            self.bot = Wallet.from_seed(PlatformWallet.objects.get(role="bot").seed)
        except (Currency.DoesNotExist, Party.DoesNotExist, PlatformWallet.DoesNotExist) as exc:
            raise CommandError(f"Demo data is missing ({exc}). Run bootstrap_demo first.") from exc
        self.signers = [self.wallets[r].address for r in ("tenant", "landlord", "arbiter")]

        net = network()
        self.stdout.write(f"Network: {net['name']} | currency: {currency.label} | deposit: {DEPOSIT} | scenarios: {', '.join(wanted)}")
        for role, party in self.parties.items():
            self.stdout.write(f"  {role:<9} {party.display_name:<24} {explorer_account(party.address)}")

        self.results = {}
        try:
            self.ensure_funds(len(wanted))
        except (LedgerError, CheckFailed) as exc:
            raise CommandError(f"Can't start: {getattr(exc, 'message', exc)}") from exc

        pending_a = self.run("A", self.scenario_a_start) if "A" in wanted else None
        if "B" in wanted:
            self.run("B", self.scenario_b)
        if "C" in wanted:
            self.run("C", self.scenario_c)
        if pending_a:
            self.run("A", lambda: self.scenario_a_finish(pending_a))

        self.stdout.write("\nRESULT  CHECK")
        failed = False
        for name, (ok, detail) in self.results.items():
            failed = failed or not ok
            mark = self.style.SUCCESS("PASS  ") if ok else self.style.ERROR("FAIL  ")
            self.stdout.write(f"{mark}  {name}" + ("" if ok else f": {detail}"))
        if failed:
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("\nAll checks passed."))

    def run(self, name, fn):
        """Run one scenario stage; record FAIL with the reason instead of aborting the others."""
        if self.results.get(SCENARIOS[name], (True,))[0] is False:
            return None
        try:
            value = fn()
            self.results.setdefault(SCENARIOS[name], (True, ""))
            return value if value is not None else True
        except LedgerError as exc:
            detail = f"{exc.engine_result}: {exc.message}"
        except CheckFailed as exc:
            detail = str(exc)
        self.results[SCENARIOS[name]] = (False, detail)
        self.stdout.write(f"[{name}] " + self.style.ERROR("FAILED ") + detail)
        return None

    def negative(self, tag, name, expected_code, fn):
        """A step that must be refused with `expected_code` and a friendly message."""
        try:
            fn()
        except LedgerError as exc:
            ok = exc.engine_result == expected_code and expected_code not in exc.message
            self.results[name] = (ok, f"got {exc.engine_result}: {exc.message}")
            self.say(tag, (self.style.SUCCESS("ok ") if ok else self.style.ERROR("FAILED ")) + f"refused as expected ({exc.engine_result}): {exc.message}")
            return
        self.results[name] = (False, "it was NOT refused")
        self.say(tag, self.style.ERROR("FAILED ") + f"{name}: it was not refused")

    def ensure_funds(self, count):
        need_xrp = services.SETTLEMENT_FUND_XRP * count + TREASURY_KEEP
        for _ in range(4):
            have = services.get_balances(self.treasury.address)["XRP"]
            if have >= need_xrp:
                break
            self.stdout.write(f"  treasury has {have} XRP, needs {need_xrp}; asking the faucet")
            try:
                generate_faucet_wallet(services.get_client(), wallet=self.treasury)
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"  faucet busy ({str(exc)[:80]}); retrying in 10s"))
                time.sleep(10)
        else:
            raise CheckFailed("the treasury is short of XRP and the faucet didn't help; wait a few minutes and rerun")
        have = self.token(self.wallets["tenant"].address)
        if have < DEPOSIT * count:
            raise CheckFailed(f"the tenant holds {have} {self.code} but needs {DEPOSIT * count}; rerun bootstrap_demo to top up")

    # ---- shared steps ----
    def open_deposit(self, tag):
        """Settlement account + escrow. -> dict describing the 'lease'."""
        tenant = self.wallets["tenant"]

        def on_step(step, tx_hash):
            self.say(tag, f"settlement account: {step}", tx_hash)

        address, _seed = services.create_settlement_account(self.treasury, self.signers, self.code, self.issuer, on_step)
        problems = services.verify_settlement_account(address, self.signers, self.code, self.issuer)
        self.expect(tag, not problems, f"settlement account {address}: master key disabled, 2-of-3 signers, trust line")
        self.say(tag, f"settlement account: {explorer_account(address)}")

        condition, fulfillment = make_condition()
        cancel_after = services.ledger_close_time() + LEASE + GRACE
        before = self.token(tenant.address)
        tx_hash, sequence = services.create_escrow(tenant, address, self.code, self.issuer, DEPOSIT, condition, cancel_after)
        self.say(tag, f"escrow created by the tenant (sequence {sequence}, automatic return after {cancel_after:%H:%M:%S} UTC)", tx_hash)

        escrow = services.get_escrow(tenant.address, sequence)
        self.expect(tag, escrow is not None, "escrow exists on the ledger")
        self.expect(
            tag,
            escrow["destination"] == address and escrow["amount"] == DEPOSIT and escrow["currency"] == self.code
            and escrow["issuer"] == self.issuer and escrow["condition"] == condition
            and escrow["cancel_after"] == cancel_after.replace(microsecond=0),
            f"escrow holds {DEPOSIT} {self.code} for the settlement account with our condition and CancelAfter",
        )
        self.expect(tag, self.token(tenant.address) == before - DEPOSIT, f"tenant balance dropped by {DEPOSIT} {self.code}")
        return {
            "settlement": address, "seed": _seed, "sequence": sequence, "condition": condition,
            "fulfillment": fulfillment, "cancel_after": cancel_after, "tenant_before": before,
        }

    def release(self, tag, lease, role):
        tenant = self.wallets["tenant"]
        tx_hash = services.finish_escrow(
            self.wallets[role], tenant.address, lease["sequence"], lease["condition"], lease["fulfillment"], lease["cancel_after"]
        )
        self.say(tag, f"escrow released by the {role}", tx_hash)
        self.expect(tag, services.get_escrow(tenant.address, lease["sequence"]) is None, "escrow is gone from the ledger")
        self.expect(tag, self.token(lease["settlement"]) == DEPOSIT, f"settlement account holds {DEPOSIT} {self.code}")

    def pay_out(self, tag, lease, payouts, signer_roles):
        """Prepare the payouts, collect two signatures for each, execute them in order."""
        prepared = services.prepare_payout_txs(lease["settlement"], payouts, self.code, self.issuer)
        self.say(tag, f"prepared {len(prepared)} payout transaction(s), sequences {[t['Sequence'] for t in prepared]}")
        for tx in prepared:
            signatures = [services.sign_prepared(tx, self.wallets[role]) for role in signer_roles]
            tx_hash = services.execute_multisigned(tx, signatures)
            self.say(tag, f"paid {tx['Amount']['value']} {self.code} to {tx['Destination']}, signed by {' + '.join(signer_roles)}", tx_hash)
        self.expect(tag, self.token(lease["settlement"]) == 0, "settlement account is empty")

    # ---- scenarios ----
    def scenario_a_start(self):
        self.stdout.write(self.style.MIGRATE_HEADING("\nA. Normal end (started; B and C run while it waits)"))
        return self.open_deposit("A")

    def scenario_a_finish(self, lease):
        self.stdout.write(self.style.MIGRATE_HEADING("\nA. Normal end (continued)"))
        tenant = self.wallets["tenant"]
        if services.ledger_close_time() <= lease["cancel_after"]:
            self.negative(
                "A", "Negative: cancelling before the deadline is refused", "escrow_not_expired",
                lambda: services.cancel_escrow(self.bot, tenant.address, lease["sequence"], lease["cancel_after"]),
            )

        deadline = lease["cancel_after"] + services.CANCEL_SAFETY
        while True:
            remaining = (deadline - services.ledger_close_time()).total_seconds()
            if remaining < 0:
                break
            self.say("A", f"waiting for the ledger clock: {int(remaining) + 1:>3} s until CancelAfter + 10 s")
            time.sleep(min(10, max(3, remaining)))

        self.negative(
            "A", "Negative: releasing after CancelAfter is refused", "escrow_expired",
            lambda: services.finish_escrow(
                tenant, tenant.address, lease["sequence"], lease["condition"], lease["fulfillment"], lease["cancel_after"]
            ),
        )
        before = self.token(tenant.address)
        tx_hash = services.cancel_escrow(self.bot, tenant.address, lease["sequence"], lease["cancel_after"])
        self.say("A", "escrow cancelled by the bot wallet", tx_hash)
        self.expect("A", services.get_escrow(tenant.address, lease["sequence"]) is None, "escrow is gone from the ledger")
        self.expect("A", self.token(tenant.address) == before + DEPOSIT, f"tenant got {DEPOSIT} {self.code} back (now {before + DEPOSIT})")
        self.expect("A", self.token(lease["settlement"]) == 0, "settlement account never received anything")

    def scenario_b(self):
        self.stdout.write(self.style.MIGRATE_HEADING("\nB. Early move-out"))
        tenant = self.wallets["tenant"]
        lease = self.open_deposit("B")
        self.release("B", lease, "tenant")

        prepared = services.prepare_payout_txs(lease["settlement"], [(tenant.address, DEPOSIT)], self.code, self.issuer)
        one = [services.sign_prepared(prepared[0], tenant)]
        self.negative(
            "B", "Negative: a payout with one signature is refused before submitting", "tefBAD_QUORUM",
            lambda: services.execute_multisigned(prepared[0], one),
        )
        self.negative(
            "B", "Negative: the ledger itself rejects a one-signature payout", "tefBAD_QUORUM",
            lambda: services.execute_multisigned(prepared[0], one, enforce_quorum=False),
        )
        self.negative(
            "B", "Negative: a payout that doesn't add up to the balance is refused", "payout_sum_mismatch",
            lambda: services.prepare_payout_txs(lease["settlement"], [(tenant.address, DEPOSIT - 1)], self.code, self.issuer),
        )
        self.pay_out("B", lease, [(tenant.address, DEPOSIT)], ["tenant", "landlord"])
        self.expect("B", self.token(tenant.address) == lease["tenant_before"], f"tenant got {DEPOSIT} {self.code} back")

    def scenario_c(self):
        self.stdout.write(self.style.MIGRATE_HEADING("\nC. Damage dispute"))
        tenant, landlord = self.wallets["tenant"], self.wallets["landlord"]
        lease = self.open_deposit("C")
        landlord_before = self.token(landlord.address)
        self.release("C", lease, "landlord")

        settlement_key = Wallet.from_seed(lease["seed"])
        self.negative(
            "C", "Negative: the settlement account's own key can't move the deposit", "tefMASTER_DISABLED",
            lambda: services.submit(
                Payment(
                    account=settlement_key.address, destination=landlord.address,
                    amount=services.IssuedCurrencyAmount(currency=self.code, issuer=self.issuer, value="1000"),
                ),
                settlement_key,
            ),
        )
        self.pay_out("C", lease, [(tenant.address, Decimal("850")), (landlord.address, Decimal("150"))], ["landlord", "arbiter"])
        self.expect("C", self.token(tenant.address) == lease["tenant_before"] - 150, f"tenant got 850 {self.code} back")
        self.expect("C", self.token(landlord.address) == landlord_before + 150, f"landlord received 150 {self.code}")


SCENARIOS = {
    "A": "Scenario A: normal end, automatic return",
    "B": "Scenario B: early move-out, 100% back to the tenant",
    "C": "Scenario C: damage dispute, 850 / 150 split",
}
