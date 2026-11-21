"""Lifecycle and API tests. Every ledger call is mocked: nothing here touches the network."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from xrpl.utils import datetime_to_ripple_time
from xrpl.wallet import Wallet

from ledger import services, xaman
from ledger.errors import ExpiredPreparedTx, LedgerError

from . import lifecycle
from .lifecycle import InvalidInput, InvalidTransition
from .models import Currency, Invite, Lease, Party, PlatformWallet, XamanRequest

S = Lease.Status
MOCKED = [
    "ledger_close_time", "create_settlement_account", "create_escrow", "get_escrow", "finish_escrow", "cancel_escrow",
    "get_token_balance", "prepare_payout_txs", "sign_prepared", "execute_multisigned", "get_signer_list",
    "get_account_flags", "get_balances", "get_transaction", "build_escrow_create", "build_escrow_finish",
    "fund_xrp", "issue_tokens", "ensure_trust_line", "has_trust_line", "faucet_top_up",
    "find_escrow_create_hash", "anchor_memo", "build_anchor_memo", "get_account_raw", "account_exists",
    "verify_multisig_blob",
]


class LedgerMockedTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        def party(name):
            return Party.objects.create(display_name=name, address=Wallet.create().address)

        cls.tenant = party("Giulia Rossi")
        cls.landlord = party("Marco Bianchi")
        cls.arbiter = party("Bari Housing Mediation")
        cls.outsider = party("Someone Else")
        for role in ("treasury", "bot", "issuer_eur"):
            wallet = Wallet.create()
            PlatformWallet.objects.create(role=role, address=wallet.address, seed=wallet.seed)
        cls.eur = Currency.objects.create(code="EUR", label="EUR (test)", issuer_address=PlatformWallet.objects.get(role="issuer_eur").address)

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
        self.ledger.anchor_memo.return_value = "ANCHORHASH"
        self.ledger.verify_multisig_blob.side_effect = lambda tx, blob, address: {**tx, "signed_by": address, "blob": blob}
        self.ledger.account_exists.return_value = True

    def _create_settlement(self, treasury, signers, code, issuer, on_step=None):
        for step in ("funded", "trust_line", "signers", "master_disabled"):
            on_step(step, f"HASH_{step}")
        return "rSettlement", "sSettlementSeed"

    def _execute(self, tx, signed):
        self.executed.append((tx, signed))
        return f"PAYHASH{len(self.executed)}"

    def make_lease(self, status=S.DRAFT, **fields):
        lease = Lease.objects.create(
            tenant=self.tenant, landlord=self.landlord, arbiter=self.arbiter, created_by=self.tenant, currency=self.eur,
            amount=Decimal("1000"), end_at=timezone.now() + timedelta(hours=1), grace_minutes=5,
            arbiter_accepted=True, tenant_approved_arbiter=True, landlord_approved_arbiter=True, status=S.DRAFT, **fields,
        )
        if status != S.DRAFT:
            lease.status, lease.settlement_address = status, "rSettlement"
            if status not in (S.SETTLEMENT_READY, S.PENDING_ACCEPTANCE, S.CANCELLED):
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



class WalletEscrowVerificationTests(LedgerMockedTestCase):
    HASH = "AB" * 32

    def found(self, lease, **changes):
        tx = {
            "TransactionType": "EscrowCreate", "Account": self.tenant.address, "Destination": "rSettlement",
            "Amount": {"currency": "EUR", "issuer": self.eur.issuer_address, "value": "1000"}, "Condition": lease.condition_hex,
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
        for amount in ({"currency": "USD", "issuer": self.eur.issuer_address, "value": "1000"}, {"currency": "EUR", "issuer": "rOther", "value": "1000"},
                       {"currency": "EUR", "issuer": self.eur.issuer_address, "value": "999"}):
            self.assertTrue(lifecycle.verify_escrow_create_tx(lease, self.found(lease, Amount=amount)))
        self.assertEqual(lifecycle.verify_escrow_create_tx(lease, self.found(lease)), [])

    def test_unknown_unvalidated_or_failed_transactions_are_refused(self):
        lease = self.make_lease(S.SETTLEMENT_READY)
        self.assertTrue(lifecycle.verify_escrow_create_tx(lease, None))
        self.assertTrue(lifecycle.verify_escrow_create_tx(lease, {**self.found(lease), "validated": False}))
        self.assertTrue(lifecycle.verify_escrow_create_tx(lease, {**self.found(lease), "result": "tecNO_LINE"}))

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

    def test_the_bot_submits_the_release_and_early_move_out_proposes_a_full_refund(self):
        lease = lifecycle.release(self.make_lease(S.ACTIVE), self.landlord, "early_move_out")
        self.assertEqual((lease.status, lease.release_reason), (S.IN_SETTLEMENT, "early_move_out"))
        self.assertEqual(self.ledger.finish_escrow.call_args.args[0].address, PlatformWallet.objects.get(role="bot").address)
        proposal = lease.proposals.get()
        self.assertEqual((proposal.tenant_amount, proposal.landlord_amount), (Decimal("1000"), Decimal("0")))
        self.assertEqual(proposal.signatures, {})
        self.assertEqual(lease.events.get(kind="released").tx_hash, "FINISHHASH")

    def test_damage_claim_makes_no_default_proposal(self):
        lease = lifecycle.release(self.make_lease(S.ACTIVE), self.landlord, "damage_claim")
        self.assertEqual(lease.status, S.IN_SETTLEMENT)
        self.assertFalse(lease.proposals.exists())


class ProposalTests(LedgerMockedTestCase):
    def setUp(self):
        super().setUp()
        self.lease = self.make_lease(S.IN_SETTLEMENT)

    def approve(self, proposal, party):
        for index in range(len(proposal.prepared_txs)):
            proposal = lifecycle.add_signature(proposal, party, index, f"BLOB{index}")
        return proposal

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

    def test_proposal_is_prepared_without_signatures(self):
        proposal = lifecycle.propose(self.lease, self.landlord, "850", 150, "Broken window")
        self.assertEqual(self.ledger.prepare_payout_txs.call_args.args[1],
                         [(self.tenant.address, Decimal("850")), (self.landlord.address, Decimal("150"))])
        self.assertEqual((proposal.status, len(proposal.prepared_txs), proposal.signatures), ("OPEN", 2, {}))
        self.assertEqual(lifecycle.next_unsigned_index(proposal, self.landlord), 0)

    def test_a_new_proposal_supersedes_the_open_one(self):
        first = lifecycle.propose(self.lease, self.landlord, "850", "150")
        second = lifecycle.propose(self.lease, self.tenant, "950", "50")
        first.refresh_from_db()
        self.assertEqual((first.status, second.status), ("SUPERSEDED", "OPEN"))
        with self.assertRaises(InvalidTransition):
            lifecycle.add_signature(first, self.arbiter, 0, "BLOB")

    def test_signatures_are_verified_per_payout_and_outsiders_refused(self):
        proposal = lifecycle.propose(self.lease, self.landlord, "850", "150")
        proposal = lifecycle.add_signature(proposal, self.landlord, 0, "BLOB0")
        self.assertEqual(self.ledger.verify_multisig_blob.call_args.args, (proposal.prepared_txs[0], "BLOB0", self.landlord.address))
        self.assertEqual(lifecycle.complete_signers(proposal), [])
        self.assertEqual(lifecycle.next_unsigned_index(proposal, self.landlord), 1)
        proposal = lifecycle.add_signature(proposal, self.landlord, 1, "BLOB1")
        self.assertEqual(lifecycle.complete_signers(proposal), [str(self.landlord.pk)])
        self.assertIsNone(lifecycle.next_unsigned_index(proposal, self.landlord))
        with self.assertRaises(InvalidTransition):
            lifecycle.add_signature(proposal, self.outsider, 0, "BLOB")
        with self.assertRaises(InvalidInput):
            lifecycle.add_signature(proposal, self.tenant, 5, "BLOB")
        self.ledger.execute_multisigned.assert_not_called()

    def test_a_bad_signature_is_not_stored(self):
        proposal = lifecycle.propose(self.lease, self.landlord, "850", "150")
        self.ledger.verify_multisig_blob.side_effect = LedgerError("signature_mismatch", "nope")
        with self.assertRaises(LedgerError):
            lifecycle.add_signature(proposal, self.landlord, 0, "BAD")
        proposal.refresh_from_db()
        self.assertEqual(proposal.signatures, {})

    def test_second_complete_signer_pays_out_in_order_and_settles(self):
        proposal = self.approve(lifecycle.propose(self.lease, self.landlord, "850", "150"), self.landlord)
        proposal = self.approve(proposal, self.arbiter)
        self.lease.refresh_from_db()
        self.assertEqual((proposal.status, self.lease.status), ("EXECUTED", S.SETTLED))
        self.assertEqual(proposal.executed_hashes, ["PAYHASH1", "PAYHASH2"])
        for index, (_tx, signed) in enumerate(self.executed):
            self.assertEqual({s["signed_by"] for s in signed}, {self.landlord.address, self.arbiter.address})
            self.assertTrue(all(s["blob"] == f"BLOB{index}" for s in signed))

    def test_expired_payouts_are_prepared_again_and_need_new_approvals(self):
        proposal = self.approve(lifecycle.propose(self.lease, self.landlord, "850", "150"), self.landlord)
        self.ledger.execute_multisigned.side_effect = ExpiredPreparedTx()
        proposal = self.approve(proposal, self.tenant)
        self.assertEqual(proposal.status, "OPEN")
        self.assertEqual(self.ledger.prepare_payout_txs.call_count, 2)
        self.assertEqual(lifecycle.complete_signers(proposal), [])
        self.assertTrue(self.lease.events.filter(kind="approvals_expired").exists())
        self.ledger.execute_multisigned.side_effect = self._execute
        proposal = self.approve(self.approve(proposal, self.tenant), self.landlord)
        self.assertEqual(proposal.status, "EXECUTED")

    def test_after_a_half_paid_split_expires_only_unpaid_payouts_need_signatures(self):
        proposal = self.approve(lifecycle.propose(self.lease, self.landlord, "850", "150"), self.landlord)
        self.ledger.execute_multisigned.side_effect = ["PAY1", ExpiredPreparedTx()]
        proposal = self.approve(proposal, self.arbiter)
        self.assertEqual((proposal.status, proposal.executed_hashes), ("OPEN", ["PAY1"]))
        self.assertEqual(lifecycle.next_unsigned_index(proposal, self.tenant), 1)
        self.assertEqual(lifecycle.complete_signers(proposal), [])

    def test_half_paid_split_can_be_retried_without_paying_twice(self):
        proposal = self.approve(lifecycle.propose(self.lease, self.landlord, "850", "150"), self.landlord)
        self.ledger.execute_multisigned.side_effect = ["PAY1", LedgerError("network_error", "offline")]
        with self.assertRaises(LedgerError):
            self.approve(proposal, self.arbiter)
        proposal.refresh_from_db()
        self.assertIn(f"retry:{proposal.pk}", lifecycle.allowed_actions(self.lease, self.tenant, timezone.now()))
        with self.assertRaises(InvalidTransition):
            lifecycle.propose(self.lease, self.tenant, "1000", "0")
        self.ledger.execute_multisigned.side_effect = ["PAY2"]
        proposal = lifecycle.retry_payout(proposal, self.tenant)
        self.assertEqual((proposal.status, proposal.executed_hashes), ("EXECUTED", ["PAY1", "PAY2"]))


class ActionsTests(LedgerMockedTestCase):
    def actions(self, lease, party, late=False):
        now = lease.cancel_after + timedelta(seconds=1) if late else timezone.now()
        return lifecycle.allowed_actions(lease, party, now)

    def test_actions_per_role_and_status(self):
        expected = {
            S.DRAFT: ([], [], []),
            S.SETTLEMENT_READY: (["lock_deposit", "cancel", "upload_evidence"], ["upload_evidence"], []),
            S.ACTIVE: (["release_early", "upload_evidence", "share_verify"], ["claim_damage", "upload_evidence", "share_verify"], ["share_verify"]),
            S.IN_SETTLEMENT: (["propose", "upload_evidence", "share_verify"], ["propose", "upload_evidence", "share_verify"], ["propose", "share_verify"]),
            S.SETTLED: (["share_verify"], ["share_verify"], ["share_verify"]),
            S.RETURNED: (["share_verify"], ["share_verify"], ["share_verify"]),
            S.FAILED: ([], [], []),
            S.CANCELLED: ([], [], []),
        }
        for status, per_role in expected.items():
            lease = self.make_lease(status)
            if status == S.FAILED:
                lease.escrow_sequence = None
            for party, wanted in zip((self.tenant, self.landlord, self.arbiter), per_role):
                with self.subTest(status=status, party=party.display_name):
                    self.assertEqual(self.actions(lease, party), wanted)
            self.assertEqual(self.actions(lease, self.outsider), [])

    def test_pending_agreement_actions(self):
        lease = self.make_lease(S.PENDING_ACCEPTANCE)
        Lease.objects.filter(pk=lease.pk).update(landlord_approved_arbiter=False)
        lease.refresh_from_db()
        self.assertEqual(self.actions(lease, self.tenant), ["change_arbiter", "cancel"])
        self.assertEqual(self.actions(lease, self.landlord), ["change_arbiter", "approve_arbiter"])
        self.assertEqual(self.actions(lease, self.arbiter), [])

    def test_no_release_actions_after_the_deadline(self):
        lease = self.make_lease(S.ACTIVE)
        self.assertNotIn("release_early", self.actions(lease, self.tenant, late=True))
        self.assertNotIn("claim_damage", self.actions(lease, self.landlord, late=True))

    def test_sign_action_until_each_party_signed_every_payout(self):
        lease = self.make_lease(S.IN_SETTLEMENT)
        proposal = lifecycle.propose(lease, self.landlord, "850", "150")
        self.assertIn(f"sign:{proposal.pk}", self.actions(lease, self.landlord))
        lifecycle.add_signature(proposal, self.landlord, 0, "B0")
        lifecycle.add_signature(proposal, self.landlord, 1, "B1")
        self.assertNotIn(f"sign:{proposal.pk}", self.actions(lease, self.landlord))
        self.assertIn(f"sign:{proposal.pk}", self.actions(lease, self.tenant))


class CancelAndExpiryTests(LedgerMockedTestCase):
    HASH = "AB" * 32

    def test_pending_agreement_past_its_return_date_is_cancelled_by_the_bot(self):
        lease = self.make_lease(S.PENDING_ACCEPTANCE)
        self.ledger.ledger_close_time.side_effect = lambda *a, **k: lease.cancel_after + timedelta(minutes=1)
        self.assertEqual(lifecycle.bot_pass(), [{"lease_id": str(lease.pk), "action": "cancelled_pending"}])
        lease.refresh_from_db()
        self.assertEqual(lease.status, S.CANCELLED)

    def test_cancel_only_before_the_deposit_is_locked(self):
        self.assertTrue(lifecycle.cancel(self.make_lease(S.SETTLEMENT_READY), "Stopped"))
        self.assertFalse(lifecycle.cancel(self.make_lease(S.ACTIVE), "Stopped"))

    def test_cancel_refused_when_the_deposit_was_just_locked(self):
        lease = self.make_lease(S.SETTLEMENT_READY)
        self.ledger.find_escrow_create_hash.return_value = "AB" * 32
        self.ledger.get_transaction.return_value = WalletEscrowVerificationTests.found(self, lease)
        self.assertFalse(lifecycle.cancel(lease, "Stopped"))
        lease.refresh_from_db()
        self.assertEqual((lease.status, lease.escrow_sequence), (S.ACTIVE, 77))

    def test_bot_returns_an_escrow_found_for_a_cancelled_lease_once(self):
        lease = self.make_lease(S.CANCELLED)
        self.ledger.ledger_close_time.side_effect = lambda *a, **k: lease.cancel_after + timedelta(minutes=1)
        self.ledger.find_escrow_create_hash.return_value = "AB" * 32
        self.ledger.get_transaction.return_value = WalletEscrowVerificationTests.found(self, lease)
        results = lifecycle.bot_pass()
        self.assertEqual([r["action"] for r in results], ["returned_after_cancel"])
        self.ledger.cancel_escrow.assert_called_once()
        self.assertEqual(self.ledger.cancel_escrow.call_args.args[1:3], (self.tenant.address, 77))
        self.assertEqual(lease.events.filter(kind="returned").count(), 1)
        self.assertEqual(lifecycle.bot_pass(), [])
        self.ledger.cancel_escrow.assert_called_once()


class AgreementTests(LedgerMockedTestCase):
    def draft(self, creator=None, role="tenant", arbiter_address=""):
        from . import agreements
        return agreements.create_draft(
            creator or self.tenant, role, currency=self.eur, amount=Decimal("1000"),
            end_at=timezone.now() + timedelta(days=30), grace_minutes=60, title="Via Sparano 12", arbiter_address=arbiter_address,
        )

    def token(self, lease, role):
        return lease.invites.get(role=role, revoked=False).token

    def test_draft_creates_invites_for_the_other_two_roles(self):
        lease = self.draft()
        self.assertEqual((lease.status, lease.tenant, lease.landlord, lease.arbiter), (S.PENDING_ACCEPTANCE, self.tenant, None, None))
        self.assertEqual(sorted(lease.invites.values_list("role", flat=True)), ["arbiter", "landlord"])
        self.ledger.create_settlement_account.assert_not_called()

    def test_landlord_can_start_too(self):
        lease = self.draft(self.landlord, "landlord")
        self.assertEqual((lease.landlord, lease.tenant), (self.landlord, None))
        self.assertEqual(sorted(lease.invites.values_list("role", flat=True)), ["arbiter", "tenant"])

    def test_everyone_accepts_and_approves_then_setup_starts(self):
        from . import agreements
        lease = self.draft()
        agreements.accept_invite(self.token(lease, "landlord"), self.landlord)
        agreements.accept_invite(self.token(lease, "arbiter"), self.arbiter)
        lease.refresh_from_db()
        self.assertEqual(lease.status, S.PENDING_ACCEPTANCE)  # the arbiter came via invite: both must approve it
        agreements.approve_arbiter(lease, self.tenant)
        agreements.approve_arbiter(lease, self.landlord)
        lease.refresh_from_db()
        self.assertEqual(lease.status, S.SETTLEMENT_READY)
        self.assertEqual(self.ledger.create_settlement_account.call_args.args[1], [p.address for p in (self.tenant, self.landlord, self.arbiter)])

    def test_arbiter_named_by_address_counts_as_the_creators_approval(self):
        from . import agreements
        lease = self.draft(arbiter_address=self.arbiter.address)
        self.assertTrue(lease.tenant_approved_arbiter)
        agreements.accept_invite(self.token(lease, "landlord"), self.landlord)  # accepting approves the named arbiter
        agreements.accept_invite(self.token(lease, "arbiter"), self.arbiter)
        lease.refresh_from_db()
        self.assertEqual(lease.status, S.SETTLEMENT_READY)

    def test_named_arbiter_invite_only_works_for_that_address(self):
        from . import agreements
        lease = self.draft(arbiter_address=self.arbiter.address)
        with self.assertRaises(InvalidTransition):
            agreements.accept_invite(self.token(lease, "arbiter"), self.outsider)

    def test_one_person_one_role_and_invites_are_single_use(self):
        from . import agreements
        lease = self.draft()
        with self.assertRaises(InvalidTransition):
            agreements.accept_invite(self.token(lease, "landlord"), self.tenant)
        token = self.token(lease, "landlord")
        agreements.accept_invite(token, self.landlord)
        agreements.accept_invite(token, self.landlord)  # idempotent for the same person
        with self.assertRaises(InvalidTransition):
            agreements.accept_invite(token, self.outsider)
        with self.assertRaises(InvalidInput):
            agreements.accept_invite("no-such-token", self.outsider)

    def test_changing_the_arbiter_resets_approvals_and_reissues_the_invite(self):
        from . import agreements
        lease = self.draft(arbiter_address=self.arbiter.address)
        old = self.token(lease, "arbiter")
        agreements.accept_invite(self.token(lease, "landlord"), self.landlord)
        agreements.change_arbiter(lease, self.landlord, self.outsider.address)
        lease.refresh_from_db()
        self.assertEqual((lease.arbiter, lease.arbiter_accepted), (self.outsider, False))
        self.assertEqual((lease.tenant_approved_arbiter, lease.landlord_approved_arbiter), (False, True))
        with self.assertRaises(InvalidInput):
            agreements.accept_invite(old, self.arbiter)
        with self.assertRaises(InvalidInput):
            agreements.change_arbiter(lease, self.tenant, self.landlord.address)  # can't be one of the two sides

    def test_decline_or_cancel_closes_the_agreement(self):
        from . import agreements
        lease = self.draft()
        agreements.decline_invite(self.token(lease, "landlord"), self.outsider)
        lease.refresh_from_db()
        self.assertEqual(lease.status, S.CANCELLED)
        other = self.draft()
        with self.assertRaises(InvalidTransition):
            agreements.cancel_by(other, self.landlord)
        agreements.cancel_by(other, self.tenant)
        other.refresh_from_db()
        self.assertEqual(other.status, S.CANCELLED)

    def test_agreement_too_close_to_its_end_is_cancelled_instead_of_started(self):
        from . import agreements
        lease = self.draft(arbiter_address=self.arbiter.address)
        # grace is 60 min and save() recomputes cancel_after, so move end_at: the return date is 1 minute away
        Lease.objects.filter(pk=lease.pk).update(end_at=timezone.now() - timedelta(minutes=59), cancel_after=timezone.now() + timedelta(minutes=1))
        agreements.accept_invite(self.token(lease, "landlord"), self.landlord)
        agreements.accept_invite(self.token(lease, "arbiter"), self.arbiter)
        lease.refresh_from_db()
        self.assertEqual(lease.status, S.CANCELLED)
        self.ledger.create_settlement_account.assert_not_called()

    def test_existing_party_cannot_decline_through_an_invite(self):
        from . import agreements
        lease = self.draft()
        with self.assertRaises(InvalidTransition) as ctx:
            agreements.decline_invite(self.token(lease, "landlord"), self.tenant)
        self.assertEqual(ctx.exception.code, "already_party")
        lease.refresh_from_db()
        self.assertEqual(lease.status, S.PENDING_ACCEPTANCE)

    def test_second_decline_is_refused(self):
        from . import agreements
        lease = self.draft()
        token = self.token(lease, "landlord")
        agreements.decline_invite(token, self.outsider)
        with self.assertRaises(InvalidTransition) as ctx:
            agreements.decline_invite(token, self.outsider)
        self.assertEqual(ctx.exception.code, "invite_used")

    def test_decline_not_recorded_when_cancel_fails(self):
        from unittest import mock
        from . import agreements
        lease = self.draft()
        invite = lease.invites.get(role="landlord")
        with mock.patch.object(agreements.lifecycle, "cancel", return_value=False):
            with self.assertRaises(InvalidTransition) as ctx:
                agreements.decline_invite(invite.token, self.outsider)
        self.assertEqual(ctx.exception.code, "wrong_status")
        invite.refresh_from_db()
        self.assertIsNone(invite.declined_at)

    def test_reaccepting_retries_a_start_that_failed(self):
        from unittest import mock
        from . import agreements
        lease = self.draft(arbiter_address=self.arbiter.address)
        agreements.accept_invite(self.token(lease, "landlord"), self.landlord)
        token = self.token(lease, "arbiter")
        with mock.patch.object(agreements, "_maybe_start", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                agreements.accept_invite(token, self.arbiter)
        lease.refresh_from_db()
        self.assertEqual(lease.status, S.PENDING_ACCEPTANCE)
        agreements.accept_invite(token, self.arbiter)  # same person retries
        lease.refresh_from_db()
        self.assertEqual(lease.status, S.SETTLEMENT_READY)

    def test_approve_arbiter_without_an_arbiter_is_refused(self):
        from . import agreements
        lease = self.draft()
        with self.assertRaises(InvalidTransition) as ctx:
            agreements.approve_arbiter(lease, self.tenant)
        self.assertEqual(ctx.exception.code, "no_arbiter")


class SigningTests(LedgerMockedTestCase):
    def setUp(self):
        super().setUp()
        self.payloads = []
        create = mock.patch.object(xaman, "create_payload", side_effect=self._create)
        self.get = mock.patch.object(xaman, "get_payload").start()
        create.start()
        self.addCleanup(mock.patch.stopall)
        self.get.return_value = {"status": "open", "account": "", "hex": "", "txid": "", "user_token": ""}

    def _create(self, txjson, **options):
        self.payloads.append((txjson, options))
        return {"uuid": f"x-{len(self.payloads)}", "deeplink": f"https://xumm.app/sign/x-{len(self.payloads)}", "pushed": False}

    def signed(self, **reply):
        self.get.return_value = {"status": "signed", "account": "", "hex": "", "txid": "", "user_token": "", **reply}

    def test_signin_creates_the_account_and_stores_the_push_token(self):
        from . import signing
        req = signing.start_signin()
        self.assertEqual(self.payloads[0], ({"TransactionType": "SignIn"}, {"submit": False, "instruction": "Sign in to safeDeposit"}))
        self.assertEqual(signing.refresh(req).status, "open")
        address = Wallet.create().address
        self.signed(account=address, user_token="tok")
        req = signing.refresh(req)
        self.assertEqual(req.status, "done")
        self.assertEqual((req.party.address, req.party.xaman_user_token), (address, "tok"))

    def test_rejected_and_expired_requests_stop(self):
        from . import signing
        for status in ("rejected", "expired"):
            req = signing.start_signin()
            self.get.return_value = {"status": status, "account": "", "hex": "", "txid": "", "user_token": ""}
            self.assertEqual(signing.refresh(req).status, status)

    def test_escrow_request_is_for_the_tenant_and_registers_once_validated(self):
        from . import signing
        lease = self.make_lease(S.SETTLEMENT_READY)
        self.ledger.build_escrow_create.return_value = {"TransactionType": "EscrowCreate", "Account": self.tenant.address,
                                                       "Sequence": 5, "Fee": "12", "LastLedgerSequence": 9, "SigningPubKey": ""}
        req = signing.start_escrow(lease, self.tenant)
        txjson, options = self.payloads[0]
        self.assertNotIn("Sequence", txjson)
        self.assertEqual((options["submit"], options["signers"]), (True, [self.tenant.address]))
        with self.assertRaises(InvalidTransition):
            signing.start_escrow(lease, self.landlord)
        self.signed(txid="AB" * 32)
        self.ledger.get_transaction.return_value = None
        self.assertEqual(signing.refresh(req).status, "signed")  # not validated yet: try again on the next poll
        with mock.patch.object(lifecycle, "register_wallet_escrow") as register:
            self.ledger.get_transaction.return_value = {"validated": True, "result": "tesSUCCESS", "tx": {}}
            self.assertEqual(signing.refresh(req).status, "done")
        register.assert_called_once_with(lease, "AB" * 32)

    def test_payout_request_walks_through_each_prepared_payout(self):
        from . import signing
        lease = self.make_lease(S.IN_SETTLEMENT)
        proposal = lifecycle.propose(lease, self.landlord, "850", "150")
        req = signing.start_payout(proposal, self.landlord)
        txjson, options = self.payloads[0]
        self.assertEqual(txjson, proposal.prepared_txs[0])
        self.assertEqual((options["submit"], options["multisign"], options["signers"]), (False, True, [self.landlord.address]))
        self.signed(hex="BLOB0")
        self.assertEqual(signing.refresh(req).status, "done")
        proposal.refresh_from_db()  # refresh() works on its own copy of the proposal
        req = signing.start_payout(proposal, self.landlord)
        self.assertEqual(req.payload_index, 1)
        self.signed(hex="BLOB1")
        signing.refresh(req)
        proposal.refresh_from_db()
        self.assertIsNone(signing.start_payout(proposal, self.landlord))

    def test_a_failed_finish_is_reported_not_raised(self):
        from . import signing
        lease = self.make_lease(S.IN_SETTLEMENT)
        proposal = lifecycle.propose(lease, self.landlord, "850", "150")
        req = signing.start_payout(proposal, self.landlord)
        self.ledger.verify_multisig_blob.side_effect = LedgerError("signature_mismatch", "Signed by someone else.")
        self.signed(hex="BAD")
        req = signing.refresh(req)
        self.assertEqual((req.status, req.error), ("failed", "Signed by someone else."))

    def test_present_never_includes_the_xaman_uuid(self):
        from . import signing
        data = signing.present(signing.start_signin())
        self.assertEqual(set(data), {"request_id", "purpose", "status", "error", "qr", "deeplink", "pushed"})
        self.assertTrue(data["qr"].startswith("data:image/svg+xml"))

    def _escrow_request(self):
        from . import signing
        lease = self.make_lease(S.SETTLEMENT_READY)
        self.ledger.build_escrow_create.return_value = {"TransactionType": "EscrowCreate", "Account": self.tenant.address}
        return signing.start_escrow(lease, self.tenant)

    def test_a_request_already_being_finished_is_left_alone(self):
        from . import signing
        from .models import XamanRequest
        req = self._escrow_request()
        XamanRequest.objects.filter(pk=req.pk).update(status="finishing", result={"txid": "AB" * 32})
        req.refresh_from_db()
        with mock.patch.dict(signing.FINISHERS, {signing.P.ESCROW_CREATE: mock.Mock()}) as _:
            finisher = signing.FINISHERS[signing.P.ESCROW_CREATE]
            self.assertEqual(signing.refresh(req).status, "finishing")
            finisher.assert_not_called()
        req.refresh_from_db()
        self.assertEqual((req.status, req.error), ("finishing", ""))

    def test_a_transient_ledger_error_keeps_the_request_signed(self):
        from . import signing
        req = self._escrow_request()
        self.signed(txid="AB" * 32)
        self.ledger.get_transaction.side_effect = LedgerError("network_error", "Can't reach the network.")
        self.assertEqual(signing.refresh(req).status, "signed")
        req.refresh_from_db()
        self.assertEqual((req.status, req.error), ("signed", ""))
        self.ledger.get_transaction.side_effect = None
        self.ledger.get_transaction.return_value = {"validated": True, "result": "tesSUCCESS", "tx": {}}
        with mock.patch.object(lifecycle, "register_wallet_escrow"):
            self.assertEqual(signing.refresh(req).status, "done")

    def test_a_transient_error_after_the_timeout_fails(self):
        from . import signing
        from datetime import timedelta
        from django.utils import timezone
        req = self._escrow_request()
        self.signed(txid="AB" * 32)
        self.ledger.get_transaction.side_effect = LedgerError("network_error", "Can't reach the network.")
        signing.refresh(req)
        req.signed_at = timezone.now() - signing.VALIDATION_TIMEOUT - timedelta(seconds=1)
        req.save(update_fields=["signed_at"])
        self.assertEqual(signing.refresh(req).status, "failed")

    def test_the_validation_timeout_counts_from_when_it_was_signed(self):
        from . import signing
        from datetime import timedelta
        from django.utils import timezone
        from .models import XamanRequest
        req = self._escrow_request()
        XamanRequest.objects.filter(pk=req.pk).update(created_at=timezone.now() - timedelta(hours=1))
        req.refresh_from_db()
        self.signed(txid="AB" * 32)
        self.ledger.get_transaction.return_value = None
        req = signing.refresh(req)
        self.assertEqual((req.status, req.error), ("signed", ""))
        self.assertIsNotNone(req.signed_at)

    def test_the_push_token_is_not_kept_in_the_result(self):
        from . import signing
        req = signing.start_signin()
        self.signed(account=Wallet.create().address, user_token="tok")
        req = signing.refresh(req)
        req.refresh_from_db()
        self.assertEqual(req.status, "done")
        self.assertNotIn("user_token", req.result)
        self.assertEqual(req.party.xaman_user_token, "tok")


class FaucetTests(LedgerMockedTestCase):
    def test_activation_sends_xrp_once_per_day_and_only_to_new_accounts(self):
        from . import faucet
        self.ledger.account_exists.return_value = False
        self.ledger.fund_xrp.return_value = "FUNDHASH"
        self.ledger.get_balances.return_value = {"XRP": Decimal("500")}
        self.assertEqual(faucet.activate(self.tenant), "FUNDHASH")
        self.assertEqual(self.ledger.fund_xrp.call_args.args[1:], (self.tenant.address, faucet.ACTIVATE_XRP))
        with self.assertRaises(InvalidTransition) as caught:
            faucet.activate(self.tenant)
        self.assertEqual(caught.exception.code, "too_soon")
        self.ledger.account_exists.return_value = True
        with self.assertRaises(InvalidTransition):
            faucet.activate(self.landlord)

    def test_tokens_need_a_trust_line_and_are_limited_per_currency(self):
        from . import faucet
        self.ledger.has_trust_line.return_value = False
        with self.assertRaises(InvalidTransition) as caught:
            faucet.grant_tokens(self.tenant, self.eur)
        self.assertEqual(caught.exception.code, "no_trust_line")
        self.ledger.has_trust_line.return_value = True
        self.ledger.issue_tokens.return_value = "TOKHASH"
        self.assertEqual(faucet.grant_tokens(self.tenant, self.eur), "TOKHASH")
        self.assertEqual(self.ledger.issue_tokens.call_args.args[1:], (self.tenant.address, "EUR", faucet.TOKEN_GRANT))
        with self.assertRaises(InvalidTransition):
            faucet.grant_tokens(self.tenant, self.eur)

    def test_a_failed_grant_does_not_use_up_the_day(self):
        from . import faucet
        self.ledger.has_trust_line.return_value = True
        self.ledger.issue_tokens.side_effect = [LedgerError("network_error", "offline"), "TOKHASH"]
        with self.assertRaises(LedgerError):
            faucet.grant_tokens(self.tenant, self.eur)
        self.assertEqual(faucet.grant_tokens(self.tenant, self.eur), "TOKHASH")

    def test_status_reports_what_is_still_possible(self):
        from . import faucet
        self.ledger.account_exists.return_value = True
        self.ledger.get_balances.return_value = {"XRP": Decimal("50"), "EUR": Decimal("0")}
        self.ledger.has_trust_line.return_value = True
        data = faucet.status(self.tenant)
        self.assertEqual(data["activated"], True)
        self.assertEqual(data["trust_lines"], {"EUR": True})
        self.assertEqual(data["available"], {"activate": False, "tokens:EUR": True})
