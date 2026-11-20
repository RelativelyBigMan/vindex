"""Lifecycle and API tests. Every ledger call is mocked: nothing here touches the network."""
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from xrpl.utils import datetime_to_ripple_time
from xrpl.wallet import Wallet

from ledger import services
from ledger.errors import ExpiredPreparedTx, LedgerError

from . import lifecycle
from .lifecycle import InvalidInput, InvalidTransition
from .models import Currency, Lease, LedgerEvent, Party, PlatformWallet, SettlementProposal

S = Lease.Status
ISSUER = "rIssuerEUR"
MOCKED = [
    "ledger_close_time", "create_settlement_account", "create_escrow", "get_escrow", "finish_escrow", "cancel_escrow",
    "get_token_balance", "prepare_payout_txs", "sign_prepared", "execute_multisigned", "get_signer_list",
    "get_account_flags", "get_balances", "get_transaction", "build_escrow_create", "build_escrow_finish",
    "fund_xrp", "issue_tokens", "ensure_trust_line", "has_trust_line", "faucet_top_up",
    "find_escrow_create_hash",
]


class LedgerMockedTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        def party(name, role):
            wallet = Wallet.create()
            return Party.objects.create(display_name=name, role=role, address=wallet.address, seed=wallet.seed)

        cls.tenant = party("Giulia Rossi", "tenant")
        cls.landlord = party("Marco Bianchi", "landlord")
        cls.arbiter = party("Bari Housing Mediation", "arbiter")
        cls.outsider = party("Someone Else", "landlord")
        for role in ("treasury", "bot"):
            wallet = Wallet.create()
            PlatformWallet.objects.create(role=role, address=wallet.address, seed=wallet.seed)
        cls.eur = Currency.objects.create(code="EUR", label="EUR (test)", issuer_address=ISSUER)

    def setUp(self):
        cache.clear()
        self.ledger = SimpleNamespace()
        for name in MOCKED:
            patcher = mock.patch.object(services, name)
            setattr(self.ledger, name, patcher.start())
            self.addCleanup(patcher.stop)
        # Background work runs inline so tests see its result.
        patcher = mock.patch.object(lifecycle, "_spawn", side_effect=lambda fn, *args: fn(*args))
        patcher.start()
        self.addCleanup(patcher.stop)

        self.balance = Decimal("1000")
        self.ledger.ledger_close_time.side_effect = lambda *a, **k: timezone.now()
        self.ledger.create_settlement_account.side_effect = self._create_settlement
        self.ledger.create_escrow.return_value = ("ESCROWHASH", 42)
        self.ledger.get_escrow.return_value = {"owner": self.tenant.address}
        self.ledger.finish_escrow.return_value = "FINISHHASH"
        self.ledger.cancel_escrow.return_value = "CANCELHASH"
        self.ledger.get_token_balance.side_effect = lambda *a, **k: self.balance
        self.ledger.prepare_payout_txs.side_effect = lambda address, payouts, code, issuer: [
            {"TransactionType": "Payment", "Destination": d, "Amount": {"value": str(a)}, "Sequence": 10 + i}
            for i, (d, a) in enumerate(payouts)
        ]
        self.ledger.sign_prepared.side_effect = lambda tx, wallet: {**tx, "signed_by": wallet.address}
        self.executed = []
        self.ledger.execute_multisigned.side_effect = self._execute
        self.ledger.get_signer_list.return_value = {
            "quorum": 2, "signers": [{"address": p.address, "weight": 1} for p in (self.tenant, self.landlord, self.arbiter)],
        }
        self.ledger.get_account_flags.return_value = {"default_ripple": False, "allow_trustline_locking": False, "disable_master": True}
        self.ledger.get_balances.return_value = {"XRP": Decimal("25"), "EUR": Decimal("5000")}
        self.ledger.find_escrow_create_hash.return_value = None

    def _create_settlement(self, treasury, signers, code, issuer, on_step=None):
        for step in ("funded", "trust_line", "signers", "master_disabled"):
            on_step(step, f"HASH_{step}")
        return "rSettlement", "sSettlementSeed"

    def _execute(self, tx, signed):
        self.executed.append((tx, signed))
        return f"PAYHASH{len(self.executed)}"

    def make_lease(self, status=S.DRAFT, **fields):
        lease = Lease.objects.create(
            tenant=self.tenant, landlord=self.landlord, arbiter=self.arbiter, currency=self.eur,
            amount=Decimal("1000"), end_at=timezone.now() + timedelta(hours=1), grace_minutes=5, **fields,
        )
        if status != S.DRAFT:
            lease.status, lease.settlement_address = status, "rSettlement"
            if status != S.SETTLEMENT_READY:
                lease.escrow_owner, lease.escrow_sequence = self.tenant.address, 42
            lease.save()
        return lease


class LeaseModelTests(LedgerMockedTestCase):
    def test_cancel_after_and_condition_are_set_on_creation(self):
        lease = self.make_lease()
        self.assertEqual(lease.cancel_after, lease.end_at + timedelta(minutes=5))
        self.assertEqual(lease.cancel_after.microsecond, 0)
        self.assertEqual(len(lease.condition_hex), 78)
        self.assertEqual(len(lease.fulfillment_hex), 72)
        condition = lease.condition_hex
        lease.save()
        self.assertEqual(lease.condition_hex, condition)


class SetupAndEscrowTests(LedgerMockedTestCase):
    def test_setup_runs_all_steps_and_becomes_ready(self):
        lease = self.make_lease()
        lifecycle.start_setup(lease)
        lease.refresh_from_db()
        self.assertEqual(lease.status, S.SETTLEMENT_READY)
        self.assertEqual(lease.setup_step, "master_disabled")
        self.assertEqual(lease.settlement_address, "rSettlement")
        self.assertEqual(lease.events.count(), 4)
        self.assertEqual(self.ledger.create_settlement_account.call_args.args[1], [p.address for p in lease.signers()])

    def test_low_treasury_is_topped_up_from_the_faucet_first(self):
        self.ledger.get_balances.return_value = {"XRP": Decimal("7.9")}
        lifecycle.start_setup(self.make_lease())
        self.ledger.faucet_top_up.assert_called_once()
        self.ledger.get_balances.return_value = {"XRP": Decimal("8")}
        lifecycle.start_setup(self.make_lease())
        self.ledger.faucet_top_up.assert_called_once()

    def test_setup_cannot_be_started_twice(self):
        lease = self.make_lease()
        lifecycle.start_setup(lease)
        with self.assertRaises(InvalidTransition):
            lifecycle.start_setup(lease)
        self.assertEqual(self.ledger.create_settlement_account.call_count, 1)

    def test_setup_error_fails_the_lease_with_the_friendly_message(self):
        self.ledger.create_settlement_account.side_effect = LedgerError("tecUNFUNDED_PAYMENT")
        lease = self.make_lease()
        lifecycle.start_setup(lease)
        lease.refresh_from_db()
        self.assertEqual(lease.status, S.FAILED)
        self.assertIn("enough", lease.error)

    def test_unexpected_setup_error_does_not_leak_details(self):
        self.ledger.create_settlement_account.side_effect = RuntimeError("secret internals")
        lease = self.make_lease()
        with self.assertLogs("core.lifecycle", level="ERROR"):
            lifecycle.start_setup(lease)
        lease.refresh_from_db()
        self.assertEqual(lease.status, S.FAILED)
        self.assertNotIn("secret", lease.error)

    def test_escrow_needs_a_ready_settlement_account(self):
        for status in (S.DRAFT, S.ACTIVE, S.IN_SETTLEMENT, S.SETTLED, S.RETURNED, S.FAILED):
            with self.assertRaises(InvalidTransition):
                lifecycle.create_escrow_demo(self.make_lease(status))
        self.ledger.create_escrow.assert_not_called()

    def test_escrow_locks_the_deposit_and_stores_the_sequence(self):
        lease = lifecycle.create_escrow_demo(self.make_lease(S.SETTLEMENT_READY))
        self.assertEqual((lease.status, lease.escrow_owner, lease.escrow_sequence), (S.ACTIVE, self.tenant.address, 42))
        args = self.ledger.create_escrow.call_args.args
        self.assertEqual(args[0].address, self.tenant.address)
        self.assertEqual(args[1:], ("rSettlement", "EUR", ISSUER, lease.amount, lease.condition_hex, lease.cancel_after))
        self.assertEqual(lease.events.get().tx_hash, "ESCROWHASH")

    def test_escrow_refused_when_the_tenant_is_short(self):
        self.balance = Decimal("999.99")
        lease = self.make_lease(S.SETTLEMENT_READY)
        with self.assertRaises(InvalidInput) as caught:
            lifecycle.create_escrow_demo(lease)
        self.assertEqual(caught.exception.code, "insufficient_funds")
        self.ledger.create_escrow.assert_not_called()


class WalletEscrowVerificationTests(LedgerMockedTestCase):
    HASH = "AB" * 32

    def found(self, lease, **changes):
        tx = {
            "TransactionType": "EscrowCreate", "Account": self.tenant.address, "Destination": "rSettlement",
            "Amount": {"currency": "EUR", "issuer": ISSUER, "value": "1000"}, "Condition": lease.condition_hex,
            "CancelAfter": datetime_to_ripple_time(lease.cancel_after), "Sequence": 77,
        }
        tx.update(changes)
        return {"hash": self.HASH, "validated": True, "result": "tesSUCCESS", "tx": tx}

    def test_matching_transaction_is_accepted(self):
        lease = self.make_lease(S.SETTLEMENT_READY)
        self.ledger.get_transaction.return_value = self.found(lease)
        lifecycle.register_wallet_escrow(lease, self.HASH.lower())
        self.assertEqual((lease.status, lease.escrow_owner, lease.escrow_sequence), (S.ACTIVE, self.tenant.address, 77))

    def test_every_mismatch_is_caught(self):
        lease = self.make_lease(S.SETTLEMENT_READY)
        wrong = {
            "TransactionType": "Payment",
            "Account": self.landlord.address,
            "Destination": self.landlord.address,
            "Amount": "1000000",
            "Condition": "A0258020" + "00" * 32 + "810120",
            "CancelAfter": datetime_to_ripple_time(lease.cancel_after) + 60,
            "FinishAfter": 1,
        }
        for field, value in wrong.items():
            with self.subTest(field=field):
                self.assertTrue(lifecycle.verify_escrow_create_tx(lease, self.found(lease, **{field: value})))
        for amount in ({"currency": "USD", "issuer": ISSUER, "value": "1000"}, {"currency": "EUR", "issuer": "rOther", "value": "1000"},
                       {"currency": "EUR", "issuer": ISSUER, "value": "999"}):
            self.assertTrue(lifecycle.verify_escrow_create_tx(lease, self.found(lease, Amount=amount)))
        self.assertEqual(lifecycle.verify_escrow_create_tx(lease, self.found(lease)), [])

    def test_unknown_unvalidated_or_failed_transactions_are_refused(self):
        lease = self.make_lease(S.SETTLEMENT_READY)
        self.assertTrue(lifecycle.verify_escrow_create_tx(lease, None))
        self.assertTrue(lifecycle.verify_escrow_create_tx(lease, {**self.found(lease), "validated": False}))
        self.assertTrue(lifecycle.verify_escrow_create_tx(lease, {**self.found(lease), "result": "tecNO_LINE"}))

    def test_escrow_already_on_the_ledger_is_adopted_instead_of_locked_twice(self):
        lease = self.make_lease(S.SETTLEMENT_READY)
        self.ledger.find_escrow_create_hash.return_value = self.HASH
        self.ledger.get_transaction.return_value = self.found(lease)
        lifecycle.create_escrow_demo(lease)
        self.assertEqual((lease.status, lease.escrow_sequence), (S.ACTIVE, 77))
        self.ledger.create_escrow.assert_not_called()

    def test_bot_recovers_a_lost_escrow_or_closes_a_lease_that_was_never_locked(self):
        lost, never = self.make_lease(S.SETTLEMENT_READY), self.make_lease(S.SETTLEMENT_READY)
        fresh = self.make_lease(S.SETTLEMENT_READY)
        Lease.objects.filter(pk=fresh.pk).update(cancel_after=fresh.cancel_after + timedelta(days=1))
        self.ledger.ledger_close_time.side_effect = lambda *a, **k: lost.cancel_after + timedelta(minutes=1)
        self.ledger.find_escrow_create_hash.side_effect = lambda owner, destination, condition: self.HASH if condition == lost.condition_hex else None
        self.ledger.get_transaction.return_value = self.found(lost)
        actions = {r["lease_id"]: r["action"] for r in lifecycle.bot_pass()}
        self.assertEqual(actions, {str(lost.pk): "returned", str(never.pk): "expired_unlocked"})
        self.assertEqual(self.ledger.cancel_escrow.call_args.args[1:3], (self.tenant.address, 77))
        statuses = {str(l.pk): l.status for l in Lease.objects.all()}
        self.assertEqual(statuses, {str(lost.pk): S.RETURNED, str(never.pk): S.FAILED, str(fresh.pk): S.SETTLEMENT_READY})

    def test_rejected_transaction_leaves_the_lease_unchanged(self):
        lease = self.make_lease(S.SETTLEMENT_READY)
        self.ledger.get_transaction.return_value = self.found(lease, Destination="rAttacker")
        with self.assertRaises(InvalidInput):
            lifecycle.register_wallet_escrow(lease, self.HASH)
        with self.assertRaises(InvalidInput):
            lifecycle.register_wallet_escrow(lease, "not-a-hash")
        lease.refresh_from_db()
        self.assertEqual(lease.status, S.SETTLEMENT_READY)


class ReleaseTests(LedgerMockedTestCase):
    def test_only_an_active_lease_can_be_released(self):
        for status in (S.DRAFT, S.SETTLEMENT_READY, S.IN_SETTLEMENT, S.SETTLED, S.RETURNED):
            with self.assertRaises(InvalidTransition):
                lifecycle.release(self.make_lease(status), self.tenant, "early_move_out")
        self.ledger.finish_escrow.assert_not_called()

    def test_arbiter_and_outsiders_cannot_release(self):
        lease = self.make_lease(S.ACTIVE)
        for party in (self.arbiter, self.outsider):
            with self.assertRaises(InvalidTransition):
                lifecycle.release(lease, party, "damage_claim")
        with self.assertRaises(InvalidInput):
            lifecycle.release(lease, self.tenant, "because")
        self.ledger.finish_escrow.assert_not_called()

    def test_release_after_the_deadline_is_refused(self):
        self.ledger.finish_escrow.side_effect = LedgerError("escrow_expired", "Too late.")
        lease = self.make_lease(S.ACTIVE)
        with self.assertRaises(InvalidTransition) as caught:
            lifecycle.release(lease, self.tenant, "early_move_out")
        self.assertEqual(caught.exception.code, "escrow_expired")
        lease.refresh_from_db()
        self.assertEqual(lease.status, S.ACTIVE)

    def test_early_move_out_proposes_everything_back_to_the_tenant(self):
        lease = lifecycle.release(self.make_lease(S.ACTIVE), self.landlord, "early_move_out")
        self.assertEqual((lease.status, lease.release_reason), (S.IN_SETTLEMENT, "early_move_out"))
        self.assertEqual(self.ledger.finish_escrow.call_args.args[0].address, self.landlord.address)
        proposal = lease.proposals.get()
        self.assertEqual((proposal.tenant_amount, proposal.landlord_amount), (Decimal("1000"), Decimal("0")))
        self.assertEqual(proposal.proposed_by, self.landlord)
        self.assertEqual(list(proposal.signatures), [str(self.landlord.pk)])
        self.assertEqual(len(proposal.prepared_txs), 1)  # the zero payout is skipped

    def test_damage_claim_makes_no_default_proposal(self):
        lease = lifecycle.release(self.make_lease(S.ACTIVE), self.landlord, "damage_claim")
        self.assertEqual(lease.status, S.IN_SETTLEMENT)
        self.assertFalse(lease.proposals.exists())


class ProposalTests(LedgerMockedTestCase):
    def setUp(self):
        super().setUp()
        self.lease = self.make_lease(S.IN_SETTLEMENT)

    def test_propose_only_in_settlement_and_only_by_a_signer(self):
        with self.assertRaises(InvalidTransition):
            lifecycle.propose(self.make_lease(S.ACTIVE), self.landlord, "850", "150")
        with self.assertRaises(InvalidTransition):
            lifecycle.propose(self.lease, self.outsider, "850", "150")

    def test_amounts_are_validated(self):
        for tenant, landlord in (("-1", "1001"), ("abc", "1"), ("850", "149"), ("850", "150.01"), (None, "1000"),
                                 ("0.0000001", "999.9999999"), (True, "999")):
            with self.subTest(tenant=tenant, landlord=landlord), self.assertRaises(InvalidInput):
                lifecycle.propose(self.lease, self.landlord, tenant, landlord)
        self.assertFalse(self.lease.proposals.exists())

    def test_proposal_is_prepared_and_signed_by_the_proposer(self):
        proposal = lifecycle.propose(self.lease, self.landlord, "850", 150, "Broken window")
        payouts = self.ledger.prepare_payout_txs.call_args.args[1]
        self.assertEqual(payouts, [(self.tenant.address, Decimal("850")), (self.landlord.address, Decimal("150"))])
        self.assertEqual(proposal.status, "OPEN")
        self.assertEqual(len(proposal.prepared_txs), 2)
        self.assertEqual(list(proposal.signatures), [str(self.landlord.pk)])
        self.assertEqual(len(proposal.signatures[str(self.landlord.pk)]), 2)

    def test_a_new_proposal_supersedes_the_open_one(self):
        first = lifecycle.propose(self.lease, self.landlord, "850", "150")
        second = lifecycle.propose(self.lease, self.tenant, "950", "50")
        first.refresh_from_db()
        self.assertEqual((first.status, second.status), ("SUPERSEDED", "OPEN"))
        with self.assertRaises(InvalidTransition):
            lifecycle.sign(first, self.arbiter)
        self.assertEqual(lifecycle.open_proposal(self.lease), second)

    def test_same_party_cannot_approve_twice_and_outsiders_never(self):
        proposal = lifecycle.propose(self.lease, self.landlord, "850", "150")
        with self.assertRaises(InvalidTransition) as caught:
            lifecycle.sign(proposal, self.landlord)
        self.assertEqual(caught.exception.code, "already_signed")
        with self.assertRaises(InvalidTransition):
            lifecycle.sign(proposal, self.outsider)
        self.ledger.execute_multisigned.assert_not_called()
        self.lease.refresh_from_db()
        self.assertEqual(self.lease.status, S.IN_SETTLEMENT)

    def test_second_distinct_signature_pays_out_in_order_and_settles(self):
        proposal = lifecycle.propose(self.lease, self.landlord, "850", "150")
        proposal = lifecycle.sign(proposal, self.arbiter)
        self.lease.refresh_from_db()
        self.assertEqual((proposal.status, self.lease.status), ("EXECUTED", S.SETTLED))
        self.assertEqual(proposal.executed_hashes, ["PAYHASH1", "PAYHASH2"])
        self.assertEqual([tx["Sequence"] for tx, _ in self.executed], [10, 11])
        for index, (_tx, signed) in enumerate(self.executed):
            self.assertEqual({s["signed_by"] for s in signed}, {self.landlord.address, self.arbiter.address})
            self.assertTrue(all(s["Sequence"] == 10 + index for s in signed))
        self.assertEqual(self.lease.events.filter(kind="payout").count(), 2)
        with self.assertRaises(InvalidTransition):
            lifecycle.sign(proposal, self.tenant)

    def test_expired_payout_is_prepared_and_signed_again_once(self):
        proposal = lifecycle.propose(self.lease, self.landlord, "850", "150")
        self.ledger.execute_multisigned.side_effect = [ExpiredPreparedTx(), "PAY1", "PAY2"]
        proposal = lifecycle.sign(proposal, self.tenant)
        self.assertEqual(proposal.status, "EXECUTED")
        self.assertEqual(self.ledger.prepare_payout_txs.call_count, 2)
        self.assertEqual(set(proposal.signatures), {str(self.landlord.pk), str(self.tenant.pk)})

    def test_payout_that_expires_twice_stays_open_and_can_be_retried(self):
        proposal = lifecycle.propose(self.lease, self.landlord, "850", "150")
        self.ledger.execute_multisigned.side_effect = [ExpiredPreparedTx(), ExpiredPreparedTx()]
        with self.assertRaises(ExpiredPreparedTx):
            lifecycle.sign(proposal, self.tenant)
        proposal.refresh_from_db()
        self.assertEqual((proposal.status, len(proposal.signatures)), ("OPEN", 2))
        self.assertIn(f"sign:{proposal.pk}", lifecycle.allowed_actions(self.lease, self.landlord, timezone.now()))
        self.ledger.execute_multisigned.side_effect = ["PAY1", "PAY2"]
        self.assertEqual(lifecycle.sign(proposal, self.landlord).status, "EXECUTED")

    def test_half_paid_split_resumes_without_paying_twice(self):
        proposal = lifecycle.propose(self.lease, self.landlord, "850", "150")
        self.ledger.execute_multisigned.side_effect = ["PAY1", LedgerError("network_error", "offline")]
        with self.assertRaises(LedgerError):
            lifecycle.sign(proposal, self.arbiter)
        with self.assertRaises(InvalidTransition):
            lifecycle.propose(self.lease, self.tenant, "1000", "0")
        self.ledger.execute_multisigned.side_effect = ["PAY2"]
        proposal = lifecycle.sign(proposal, self.arbiter)
        self.assertEqual((proposal.status, proposal.executed_hashes), ("EXECUTED", ["PAY1", "PAY2"]))
        self.assertEqual(self.ledger.execute_multisigned.call_count, 3)


class BotTests(LedgerMockedTestCase):
    def expired_lease(self):
        lease = self.make_lease(S.ACTIVE)
        self.ledger.ledger_close_time.side_effect = lambda *a, **k: lease.cancel_after + timedelta(seconds=11)
        return lease

    def test_nothing_happens_before_the_deadline_plus_margin(self):
        lease = self.make_lease(S.ACTIVE)
        self.ledger.ledger_close_time.side_effect = lambda *a, **k: lease.cancel_after + timedelta(seconds=10)
        self.assertEqual(lifecycle.bot_pass(), [])
        self.ledger.cancel_escrow.assert_not_called()

    def test_expired_escrow_is_cancelled_from_the_bot_wallet(self):
        lease = self.expired_lease()
        result = lifecycle.bot_pass()
        lease.refresh_from_db()
        self.assertEqual(lease.status, S.RETURNED)
        self.assertEqual(result, [{"lease_id": str(lease.pk), "action": "returned", "tx_hash": "CANCELHASH"}])
        self.assertEqual(self.ledger.cancel_escrow.call_args.args[0].address, PlatformWallet.objects.get(role="bot").address)
        self.assertEqual(lease.events.get().summary, "Deposit returned to Giulia Rossi automatically")
        self.assertEqual(lifecycle.bot_pass(), [])

    def test_vanished_escrow_is_reconciled_from_the_ledger(self):
        self.ledger.get_escrow.return_value = None
        returned = self.expired_lease()
        self.balance = Decimal("0")
        lifecycle.bot_pass()
        returned.refresh_from_db()
        self.assertEqual(returned.status, S.RETURNED)

        released = self.expired_lease()
        self.balance = Decimal("1000")
        lifecycle.bot_pass()
        released.refresh_from_db()
        self.assertEqual(released.status, S.IN_SETTLEMENT)
        self.ledger.cancel_escrow.assert_not_called()

    def test_one_failing_lease_does_not_stop_the_pass(self):
        first, second = self.expired_lease(), self.make_lease(S.ACTIVE)
        self.ledger.ledger_close_time.side_effect = lambda *a, **k: second.cancel_after + timedelta(minutes=1)
        self.ledger.cancel_escrow.side_effect = [LedgerError("network_error", "offline"), "CANCELHASH"]
        with self.assertLogs("core.lifecycle", level="WARNING"):
            actions = sorted(r["action"] for r in lifecycle.bot_pass())
        self.assertEqual(actions, ["error", "returned"])
        self.assertEqual(sorted(Lease.objects.filter(pk__in=[first.pk, second.pk]).values_list("status", flat=True)), [S.ACTIVE, S.RETURNED])


class ActionsTests(LedgerMockedTestCase):
    def actions(self, lease, party, late=False):
        now = lease.cancel_after + timedelta(seconds=1) if late else timezone.now()
        return lifecycle.allowed_actions(lease, party, now)

    def test_actions_per_role_and_status(self):
        expected = {
            S.DRAFT: ([], [], []),
            S.SETTLEMENT_READY: (["lock_deposit", "upload_evidence"], ["upload_evidence"], []),
            S.ACTIVE: (["release_early", "upload_evidence", "share_verify"], ["claim_damage", "upload_evidence", "share_verify"], ["share_verify"]),
            S.IN_SETTLEMENT: (["propose", "upload_evidence", "share_verify"], ["propose", "upload_evidence", "share_verify"], ["propose", "share_verify"]),
            S.SETTLED: (["share_verify"], ["share_verify"], ["share_verify"]),
            S.RETURNED: (["share_verify"], ["share_verify"], ["share_verify"]),
            S.FAILED: ([], [], []),
        }
        for status, per_role in expected.items():
            lease = self.make_lease(status)
            if status == S.FAILED:
                lease.escrow_sequence = None
            for party, wanted in zip((self.tenant, self.landlord, self.arbiter), per_role):
                with self.subTest(status=status, role=party.role):
                    self.assertEqual(self.actions(lease, party), wanted)
            self.assertEqual(self.actions(lease, self.outsider), [])

    def test_no_release_actions_after_the_deadline(self):
        lease = self.make_lease(S.ACTIVE)
        self.assertNotIn("release_early", self.actions(lease, self.tenant, late=True))
        self.assertNotIn("claim_damage", self.actions(lease, self.landlord, late=True))

    def test_sign_action_goes_to_those_who_have_not_signed(self):
        lease = self.make_lease(S.IN_SETTLEMENT)
        proposal = lifecycle.propose(lease, self.landlord, "850", "150")
        self.assertNotIn(f"sign:{proposal.pk}", self.actions(lease, self.landlord))
        self.assertIn(f"sign:{proposal.pk}", self.actions(lease, self.tenant))
        self.assertIn(f"sign:{proposal.pk}", self.actions(lease, self.arbiter))


class ApiTests(LedgerMockedTestCase):
    def post(self, url, data=None):
        return self.client.post(url, data=json.dumps(data or {}), content_type="application/json")

    def act_as(self, party):
        self.assertEqual(self.post("/api/demo/persona/", {"party_id": party.pk}).status_code, 200)

    def new_lease(self, **changes):
        body = {"landlord_id": self.landlord.pk, "arbiter_id": self.arbiter.pk, "currency": "EUR", "amount": "1000",
                "end_in_minutes": 60, "grace_minutes": 5, **changes}
        return self.post("/api/leases/", body)

    def test_persona_defaults_to_the_tenant_and_can_be_switched(self):
        me = self.client.get("/api/me/")
        self.assertEqual((me.json()["party_id"], me.json()["role"]), (self.tenant.pk, "tenant"))
        self.assertEqual(me.json()["balances"], {"XRP": "25", "EUR": "5000"})
        self.assertIn("csrftoken", me.cookies)
        self.act_as(self.arbiter)
        self.assertEqual(self.client.get("/api/me/").json()["role"], "arbiter")
        self.assertEqual(self.post("/api/demo/persona/", {"party_id": 99999}).status_code, 400)
        self.assertEqual(len(self.client.get("/api/demo/personas/").json()["personas"]), 4)

    def test_demo_endpoints_are_off_outside_demo_mode(self):
        with self.settings(DEMO_MODE=False):
            for url in ("/api/demo/persona/", "/api/demo/fund/", "/api/demo/run-bot/"):
                self.assertEqual(self.post(url, {"party_id": self.tenant.pk}).json()["code"], "demo_disabled")
            self.assertEqual(self.client.get("/api/me/").json()["code"], "no_persona")

    def test_csrf_is_enforced_on_posts(self):
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post("/api/demo/run-bot/", data="{}", content_type="application/json").status_code, 403)
        token = strict.get("/api/me/").cookies["csrftoken"].value
        response = strict.post("/api/demo/run-bot/", data="{}", content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)

    def test_create_lease_validates_input(self):
        bad = [
            {"amount": "0"}, {"amount": "-5"}, {"amount": "abc"}, {"currency": "GBP"}, {"landlord_id": self.arbiter.pk},
            {"arbiter_id": 99999}, {"end_in_minutes": 0}, {"end_in_minutes": 1, "grace_minutes": 0},
            {"end_in_minutes": None, "end_date": "2020-01-01"}, {"end_in_minutes": None, "end_date": "soon"},
            {"grace_minutes": -1},
        ]
        for change in bad:
            with self.subTest(change=change):
                response = self.new_lease(**change)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(set(response.json()), {"error", "code"})
        self.assertFalse(Lease.objects.exists())
        self.act_as(self.landlord)
        self.assertEqual(self.new_lease().status_code, 409)

    def test_create_lease_starts_setup_and_lists_only_for_its_parties(self):
        response = self.new_lease()
        self.assertEqual(response.status_code, 201)
        lease_id = response.json()["lease_id"]
        detail = self.client.get(f"/api/leases/{lease_id}/").json()
        self.assertEqual((detail["status"], detail["setup_step"], detail["actions"]), ("SETTLEMENT_READY", "master_disabled", ["lock_deposit", "upload_evidence"]))
        self.assertEqual((detail["amount"], detail["currency_label"]), ("1000", "EUR (test)"))
        self.assertEqual([p["role"] for p in detail["parties"]], ["tenant", "landlord", "arbiter"])
        self.assertEqual((detail["settlement"]["quorum"], detail["settlement"]["master_disabled"], detail["settlement"]["balance"]), (2, True, "1000"))
        self.assertEqual([s["role"] for s in detail["settlement"]["signers"]], ["tenant", "landlord", "arbiter"])
        self.assertIsNone(detail["escrow"])
        self.assertEqual(len(detail["events"]), 4)
        self.assertTrue(detail["events"][0]["explorer_url"].endswith("/transactions/HASH_master_disabled"))
        self.assertTrue(3500 < detail["seconds_to_cancel_after"] <= 3900)

        self.assertEqual(len(self.client.get("/api/leases/").json()["leases"]), 1)
        self.assertEqual(len(self.client.get("/api/leases/?status=ACTIVE").json()["leases"]), 0)
        self.act_as(self.outsider)
        self.assertEqual(self.client.get("/api/leases/").json()["leases"], [])
        self.assertEqual(self.client.get(f"/api/leases/{lease_id}/").status_code, 404)

    def test_secrets_never_appear_in_lease_json(self):
        lease = self.make_lease(S.ACTIVE, settlement_seed="sSettlementSeed")
        lifecycle.record(lease, "escrow_created", "locked", "HASH")
        for url in (f"/api/leases/{lease.pk}/", f"/api/leases/{lease.pk}/public/", "/api/leases/"):
            body = self.client.get(url).content.decode()
            self.assertNotIn(lease.fulfillment_hex, body)
            self.assertNotIn("sSettlementSeed", body)
            self.assertNotIn(self.tenant.seed, body)
            self.assertNotIn("fulfillment", body.lower())
        self.client.logout()
        self.assertEqual(self.client.get(f"/api/leases/{lease.pk}/public/").json()["escrow_sequence"], 42)

    def test_release_tx_is_for_tenant_and_landlord_only(self):
        lease = self.make_lease(S.ACTIVE)
        self.ledger.build_escrow_finish.return_value = {"TransactionType": "EscrowFinish"}
        self.assertEqual(self.client.get(f"/api/leases/{lease.pk}/release-tx/").status_code, 200)
        self.assertEqual(self.ledger.build_escrow_finish.call_args.args[0], self.tenant.address)
        self.act_as(self.arbiter)
        self.assertEqual(self.client.get(f"/api/leases/{lease.pk}/release-tx/").status_code, 409)
        self.act_as(self.outsider)
        self.assertEqual(self.client.get(f"/api/leases/{lease.pk}/release-tx/").status_code, 404)
        self.assertEqual(self.ledger.build_escrow_finish.call_count, 1)

    def test_escrow_tx_and_wallet_mode(self):
        lease = self.make_lease(S.SETTLEMENT_READY)
        self.ledger.build_escrow_create.return_value = {"TransactionType": "EscrowCreate"}
        self.assertEqual(self.client.get(f"/api/leases/{lease.pk}/escrow-tx/").json()["tx"]["TransactionType"], "EscrowCreate")
        self.ledger.get_transaction.return_value = None
        response = self.post(f"/api/leases/{lease.pk}/escrow/", {"mode": "wallet", "tx_hash": "AB" * 32})
        self.assertEqual((response.status_code, response.json()["code"]), (400, "escrow_mismatch"))
        active = self.make_lease(S.ACTIVE)
        response = self.post(f"/api/leases/{active.pk}/release/", {"reason": "early_move_out", "mode": "wallet", "tx_hash": "AB" * 32})
        self.assertEqual(response.status_code, 501)

    def test_actions_are_enforced_and_errors_follow_the_contract(self):
        lease = self.make_lease(S.ACTIVE)
        response = self.post(f"/api/leases/{lease.pk}/release/", {"reason": "damage_claim"})  # tenant can't claim damage
        self.assertEqual((response.status_code, response.json()["code"]), (409, "not_allowed"))
        self.assertEqual(self.post(f"/api/leases/{lease.pk}/escrow/", {"mode": "demo"}).status_code, 409)
        self.assertEqual(self.post(f"/api/leases/{lease.pk}/proposals/", {"tenant_amount": 1, "landlord_amount": 2}).status_code, 409)
        self.assertEqual(self.client.post(f"/api/leases/{lease.pk}/release/", data="{nope", content_type="application/json").json()["code"], "bad_json")
        self.assertEqual(self.client.get(f"/api/leases/{lease.pk}/release/").status_code, 405)
        self.ledger.finish_escrow.side_effect = LedgerError("tecNO_PERMISSION")
        response = self.post(f"/api/leases/{lease.pk}/release/", {"reason": "early_move_out"})
        self.assertEqual((response.status_code, response.json()["code"]), (400, "tecNO_PERMISSION"))
        self.ledger.finish_escrow.side_effect = RuntimeError("boom with internals")
        with self.assertLogs("core.api", level="ERROR"):
            response = self.post(f"/api/leases/{lease.pk}/release/", {"reason": "early_move_out"})
        self.assertEqual((response.status_code, response.json()["code"]), (500, "server_error"))
        self.assertNotIn("boom", response.json()["error"])

    def test_damage_dispute_end_to_end(self):
        lease_id = self.new_lease().json()["lease_id"]
        url = f"/api/leases/{lease_id}/"
        self.assertEqual(self.post(url + "escrow/", {"mode": "demo"}).json()["status"], "ACTIVE")

        self.act_as(self.landlord)
        state = self.post(url + "release/", {"reason": "damage_claim", "mode": "demo"}).json()
        self.assertEqual((state["status"], state["release_reason"], state["proposals"]), ("IN_SETTLEMENT", "damage_claim", []))
        self.assertEqual(self.post(url + "proposals/", {"tenant_amount": "850", "landlord_amount": "100"}).json()["code"], "payout_sum_mismatch")
        created = self.post(url + "proposals/", {"tenant_amount": "850", "landlord_amount": "150", "reason": "Broken window"})
        self.assertEqual(created.status_code, 201)
        proposal_id = created.json()["proposal_id"]
        proposal = created.json()["lease"]["proposals"][0]
        self.assertEqual(([p["role"] for p in proposal["signed_by"]], [p["role"] for p in proposal["still_needed"]]), (["landlord"], ["tenant", "arbiter"]))
        self.assertEqual(self.post(f"/api/proposals/{proposal_id}/sign/").json()["code"], "already_signed")

        self.act_as(self.arbiter)
        self.assertIn(f"sign:{proposal_id}", self.client.get(url).json()["actions"])
        signed = self.post(f"/api/proposals/{proposal_id}/sign/").json()
        self.assertEqual((signed["status"], signed["lease"]["status"]), ("EXECUTED", "SETTLED"))
        final = signed["lease"]["proposals"][0]
        self.assertEqual((final["signatures_count"], len(final["executed"]), final["still_needed"]), (2, 2, []))
        self.assertEqual(signed["lease"]["events"][0]["kind"], "settled")
        self.assertEqual(signed["lease"]["actions"], ["share_verify"])

    def test_run_bot_endpoint_returns_expired_deposits(self):
        lease = self.make_lease(S.ACTIVE)
        self.ledger.ledger_close_time.side_effect = lambda *a, **k: lease.cancel_after + timedelta(seconds=30)
        processed = self.post("/api/demo/run-bot/").json()["processed"]
        self.assertEqual(processed[0]["action"], "returned")
        state = self.client.get(f"/api/leases/{lease.pk}/").json()
        self.assertEqual((state["status"], state["seconds_to_cancel_after"], state["escrow"]["exists"]), ("RETURNED", 0, False))

    def test_wallet_link_creates_a_keyless_party(self):
        address = Wallet.create().address
        self.assertEqual(self.post("/api/wallet/link/", {"address": "nope", "role": "tenant"}).status_code, 400)
        linked = self.post("/api/wallet/link/", {"address": address, "role": "landlord"}).json()
        party = Party.objects.get(address=address)
        self.assertEqual((party.seed, party.wallet_type, linked["party_id"]), (None, "gemwallet", party.pk))
        self.assertEqual(self.post("/api/wallet/link/", {"address": address, "role": "tenant"}).status_code, 409)

    def test_evidence_upload_checks_the_file(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        import hashlib, tempfile

        lease = self.make_lease(S.ACTIVE)
        png = b"\x89PNG\r\n\x1a\n" + b"pixels" * 10
        url = f"/api/leases/{lease.pk}/evidence/"
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            bad = self.client.post(url, {"kind": "damage", "file": SimpleUploadedFile("x.png", b"<html>not a photo")})
            self.assertEqual(bad.json()["code"], "bad_file_type")
            self.assertEqual(self.client.post(url, {"kind": "selfie", "file": SimpleUploadedFile("x.png", png)}).status_code, 400)
            good = self.client.post(url, {"kind": "damage", "file": SimpleUploadedFile("../../evil.png", png)})
            self.assertEqual(good.status_code, 201)
            self.assertEqual(good.json()["sha256"], hashlib.sha256(png).hexdigest())
            self.assertNotIn("evil", good.json()["url"])
            self.act_as(self.arbiter)
            self.assertEqual(self.client.post(url, {"kind": "damage", "file": SimpleUploadedFile("x.png", png)}).status_code, 409)


class DemoResetTests(LedgerMockedTestCase):
    def test_reset_archives_leases_without_touching_the_ledger(self):
        lease = self.make_lease(S.ACTIVE)
        call_command("demo_reset", stdout=mock.MagicMock())
        lease.refresh_from_db()
        self.assertTrue(lease.archived)
        self.assertEqual(lease.status, S.ACTIVE)
        self.assertEqual(self.client.get("/api/leases/").json()["leases"], [])
        self.assertEqual(self.client.get(f"/api/leases/{lease.pk}/").status_code, 200)
        for name in MOCKED:
            if name not in ("ledger_close_time", "get_signer_list", "get_account_flags", "get_token_balance", "get_escrow"):
                getattr(self.ledger, name).assert_not_called()
        self.assertFalse(LedgerEvent.objects.exists())
