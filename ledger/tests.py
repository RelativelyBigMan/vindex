import hashlib

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from . import client
from .conditions import encode_preimage, make_condition
from .errors import LedgerError


class ConditionTests(SimpleTestCase):
    def test_empty_preimage_known_vector(self):
        condition, fulfillment = encode_preimage(b"")
        self.assertEqual(condition, "A0258020E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855810100")
        self.assertEqual(fulfillment, "A0028000")

    def test_32_byte_preimage_layout(self):
        preimage = bytes(range(32))
        condition, fulfillment = encode_preimage(preimage)
        self.assertEqual(fulfillment, "A0228020" + preimage.hex().upper())
        self.assertEqual(condition, "A0258020" + hashlib.sha256(preimage).hexdigest().upper() + "810120")

    def test_make_condition_is_random_and_consistent(self):
        c1, f1 = make_condition()
        c2, f2 = make_condition()
        self.assertNotEqual(f1, f2)
        self.assertEqual(len(f1), 72)
        self.assertEqual(len(c1), 78)
        preimage = bytes.fromhex(f1[8:])
        self.assertEqual(c1[8:72], hashlib.sha256(preimage).hexdigest().upper())

    def test_long_form_length_and_limit(self):
        _, fulfillment = encode_preimage(b"\x01" * 127)
        self.assertTrue(fulfillment.startswith("A08181807F"))
        with self.assertRaises(ValueError):
            encode_preimage(b"\x01" * 128)


class ClientGuardTests(SimpleTestCase):
    def test_mainnet_urls_are_detected(self):
        for url in ("https://s1.ripple.com:51234/", "wss://xrplcluster.com", "https://livenet.xrpl.org",
                    "https://altnet.rippletest.net.evil.example/"):
            self.assertTrue(client.looks_like_mainnet(url), url)
        for net in client.NETWORKS.values():
            for field in ("json_rpc", "ws", "explorer", "faucet"):
                self.assertFalse(client.looks_like_mainnet(net[field]), net[field])

    @override_settings(XRPL_NETWORK="mainnet")
    def test_unknown_network_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            client.assert_not_mainnet()

    def test_explorer_links(self):
        self.assertEqual(client.explorer_tx("ABC"), "https://testnet.xrpl.org/transactions/ABC")
        self.assertEqual(client.explorer_account("rX"), "https://testnet.xrpl.org/accounts/rX")


class ErrorTests(SimpleTestCase):
    def test_friendly_messages(self):
        for code in ("tecNO_PERMISSION", "tecNO_LINE", "tecUNFUNDED_PAYMENT", "tecNO_DST", "tecCRYPTOCONDITION_ERROR",
                     "tefBAD_QUORUM", "tefMAX_LEDGER", "terPRE_SEQ", "temDISABLED"):
            self.assertNotIn(code, LedgerError(code).message)

    def test_from_exception_extracts_engine_result(self):
        err = LedgerError.from_exception(Exception("Transaction failed: temDISABLED"))
        self.assertEqual(err.engine_result, "temDISABLED")
        self.assertEqual(LedgerError.from_exception(Exception("boom")).engine_result, "network_error")


# ---- Patch 2: payout validation, deadline guards, signature checks (no network) ----
from datetime import datetime, timedelta, timezone  # noqa: E402
from decimal import Decimal  # noqa: E402
from unittest import mock  # noqa: E402

from xrpl.wallet import Wallet  # noqa: E402

from . import services  # noqa: E402
from .errors import ExpiredPreparedTx  # noqa: E402

NOW = datetime(2026, 11, 20, 21, 0, 0, tzinfo=timezone.utc)


class PayoutValidationTests(SimpleTestCase):
    def code(self, payouts, balance):
        with self.assertRaises(LedgerError) as ctx:
            services.validate_payouts(payouts, balance)
        return ctx.exception.engine_result

    def test_exact_split_passes(self):
        self.assertEqual(
            services.validate_payouts([("rA", "850"), ("rB", "150")], Decimal("1000")),
            [("rA", Decimal("850")), ("rB", Decimal("150"))],
        )

    def test_wrong_sum_refused(self):
        self.assertEqual(self.code([("rA", "850"), ("rB", "100")], "1000"), "payout_sum_mismatch")
        self.assertEqual(self.code([("rA", "850"), ("rB", "151")], "1000"), "payout_sum_mismatch")

    def test_zero_amounts_are_skipped(self):
        self.assertEqual(services.validate_payouts([("rA", "1000"), ("rB", "0")], "1000"), [("rA", Decimal("1000"))])
        self.assertEqual(services.validate_payouts([("rA", "0.00"), ("rB", 1000)], "1000"), [("rB", Decimal("1000"))])

    def test_empty_settlement_refused(self):
        self.assertEqual(self.code([("rA", "0"), ("rB", "0")], "0"), "nothing_to_pay")

    def test_negative_refused_even_if_sum_matches(self):
        self.assertEqual(self.code([("rA", "1100"), ("rB", "-100")], "1000"), "bad_amount")

    def test_decimal_precision_is_exact(self):
        # 0.1 + 0.2 == 0.3 only in Decimal; with floats this split would be refused (or worse, accepted wrongly).
        self.assertEqual(len(services.validate_payouts([("rA", "0.1"), ("rB", "0.2")], "0.3")), 2)
        self.assertEqual(self.code([("rA", "333.33"), ("rB", "666.66")], "1000"), "payout_sum_mismatch")
        self.assertEqual(self.code([("rA", "999.999999999999"), ("rB", "0.000000000002")], "1000"), "payout_sum_mismatch")
        self.assertEqual(len(services.validate_payouts([("rA", "333.33"), ("rB", "666.67")], "1000.00")), 2)

    def test_floats_and_junk_refused(self):
        self.assertEqual(self.code([("rA", 1000.0)], "1000"), "bad_amount")
        self.assertEqual(self.code([("rA", "abc")], "1000"), "bad_amount")
        self.assertEqual(self.code([("rA", "NaN")], "1000"), "bad_amount")

    def test_too_many_significant_digits_refused(self):
        self.assertEqual(self.code([("rA", "1234567890.1234567")], "1234567890.1234567"), "bad_amount")

    def test_token_value_formatting(self):
        self.assertEqual(services.token_value(Decimal("1000")), "1000")
        self.assertEqual(services.token_value(Decimal("1E+3")), "1000")
        self.assertEqual(services.token_value("150.50"), "150.5")

    def test_prepare_uses_live_balance_consecutive_sequences_and_long_window(self):
        a, b, s = Wallet.create().address, Wallet.create().address, Wallet.create().address
        with mock.patch.object(services, "get_client"), \
                mock.patch.object(services, "get_token_balance", return_value=Decimal("1000")), \
                mock.patch.object(services, "_request", return_value={"account_data": {"Sequence": 7}}), \
                mock.patch.object(services, "latest_validated_ledger_index", return_value=500), \
                mock.patch.object(services, "autofill", side_effect=lambda tx, client, signers_count: tx) as fill:
            txs = services.prepare_payout_txs(s, [(a, "850"), (b, "150"), (a, "0")], "EUR", b)
            with self.assertRaises(LedgerError):
                services.prepare_payout_txs(s, [(a, "850"), (b, "149")], "EUR", b)
        self.assertEqual([t["Sequence"] for t in txs], [7, 8])
        self.assertEqual({t["LastLedgerSequence"] for t in txs}, {1500})
        self.assertEqual([t["Amount"]["value"] for t in txs], ["850", "150"])
        self.assertTrue(all(c.kwargs["signers_count"] == 2 for c in fill.call_args_list))


class DeadlineGuardTests(SimpleTestCase):
    """The guards must refuse BEFORE anything is submitted, using the ledger's clock."""

    def setUp(self):
        self.wallet = Wallet.create()
        self.owner = Wallet.create().address
        for name in ("get_client", "submit"):
            patcher = mock.patch.object(services, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.submit.return_value = {"hash": "ABC"}

    def at(self, moment):
        return mock.patch.object(services, "ledger_close_time", return_value=moment)

    def finish(self, cancel_after):
        return services.finish_escrow(self.wallet, self.owner, 5, "A0", "A1", cancel_after=cancel_after)

    def test_finish_allowed_before_cancel_after(self):
        with self.at(NOW - timedelta(seconds=1)):
            self.assertEqual(self.finish(NOW), "ABC")
        self.submit.assert_called_once()

    def test_finish_refused_at_and_after_cancel_after(self):
        for moment in (NOW, NOW + timedelta(seconds=1), NOW + timedelta(days=1)):
            with self.at(moment), self.assertRaises(LedgerError) as ctx:
                self.finish(NOW)
            self.assertEqual(ctx.exception.engine_result, "escrow_expired")
            self.assertNotIn("escrow_expired", ctx.exception.message)
        self.submit.assert_not_called()

    def test_finish_reads_cancel_after_from_ledger_when_not_given(self):
        with self.at(NOW + timedelta(seconds=5)), \
                mock.patch.object(services, "get_escrow", return_value={"cancel_after": NOW}), \
                self.assertRaises(LedgerError):
            self.finish(None)
        self.submit.assert_not_called()

    def test_cancel_refused_until_ten_seconds_past(self):
        for moment in (NOW - timedelta(minutes=1), NOW, NOW + timedelta(seconds=10)):
            with self.at(moment), self.assertRaises(LedgerError) as ctx:
                services.cancel_escrow(self.wallet, self.owner, 5, NOW)
            self.assertEqual(ctx.exception.engine_result, "escrow_not_expired")
        self.submit.assert_not_called()

    def test_cancel_allowed_after_margin(self):
        with self.at(NOW + timedelta(seconds=11)):
            self.assertEqual(services.cancel_escrow(self.wallet, self.owner, 5, NOW), "ABC")
        self.submit.assert_called_once()

    def test_create_refused_when_deadline_already_passed(self):
        with self.at(NOW), self.assertRaises(LedgerError) as ctx:
            services.create_escrow(self.wallet, self.owner, "EUR", self.owner, "1000", "A0", NOW)
        self.assertEqual(ctx.exception.engine_result, "deadline_passed")
        self.submit.assert_not_called()

    def test_naive_deadlines_refused(self):
        with self.at(NOW), self.assertRaises(LedgerError) as ctx:
            services.cancel_escrow(self.wallet, self.owner, 5, datetime(2026, 11, 20, 21, 0, 0))
        self.assertEqual(ctx.exception.engine_result, "bad_deadline")


class MultisignGuardTests(SimpleTestCase):
    def setUp(self):
        self.signers = [Wallet.create() for _ in range(3)]
        settlement, dest = Wallet.create().address, Wallet.create().address
        self.tx = {
            "Account": settlement, "TransactionType": "Payment", "Fee": "36", "Sequence": 7,
            "LastLedgerSequence": 1500, "SigningPubKey": "", "Destination": dest,
            "Amount": {"currency": "EUR", "issuer": dest, "value": "1000"},
        }
        for name in ("get_client", "_send_signed"):
            patcher = mock.patch.object(services, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self._send_signed.return_value = {"hash": "ABC"}
        patcher = mock.patch.object(services, "latest_validated_ledger_index", return_value=600)
        self.latest = patcher.start()
        self.addCleanup(patcher.stop)

    def sig(self, i, tx=None):
        return services.sign_prepared(tx or self.tx, self.signers[i])

    def test_one_signature_refused_before_submitting(self):
        with self.assertRaises(LedgerError) as ctx:
            services.execute_multisigned(self.tx, [self.sig(0)])
        self.assertEqual(ctx.exception.engine_result, "tefBAD_QUORUM")
        self._send_signed.assert_not_called()

    def test_same_signer_twice_is_still_one(self):
        with self.assertRaises(LedgerError):
            services.execute_multisigned(self.tx, [self.sig(0), self.sig(0)])
        self._send_signed.assert_not_called()

    def test_two_signers_submit_with_both_signatures(self):
        self.assertEqual(services.execute_multisigned(self.tx, [self.sig(0), self.sig(2)]), "ABC")
        combined = self._send_signed.call_args.args[0]
        self.assertEqual(len(combined.signers), 2)
        self.assertEqual(combined.sequence, 7)

    def test_signature_over_a_different_transaction_refused(self):
        other = dict(self.tx, Amount=dict(self.tx["Amount"], value="999"))
        with self.assertRaises(LedgerError) as ctx:
            services.execute_multisigned(self.tx, [self.sig(0), self.sig(1, other)])
        self.assertEqual(ctx.exception.engine_result, "signature_mismatch")
        self._send_signed.assert_not_called()

    def test_expired_prepared_tx(self):
        self.latest.return_value = 1500
        with self.assertRaises(ExpiredPreparedTx) as ctx:
            services.execute_multisigned(self.tx, [self.sig(0), self.sig(1)])
        self.assertEqual(ctx.exception.engine_result, "tefMAX_LEDGER")
        self._send_signed.assert_not_called()

    def test_settlement_needs_three_distinct_signers(self):
        a = self.signers[0].address
        with self.assertRaises(LedgerError) as ctx:
            services.create_settlement_account(self.signers[0], [a, a, self.signers[1].address], "EUR", a)
        self.assertEqual(ctx.exception.engine_result, "bad_signers")


class MemoTests(SimpleTestCase):
    def test_memo_round_trip_and_size_limit(self):
        data = {"lease": "abc", "kind": "damage", "sha256": "ab" * 32}
        from xrpl.models.transactions import AccountSet
        from xrpl.wallet import Wallet

        tx = AccountSet(account=Wallet.create().address, memos=[services._memo("safedeposit/evidence", data)]).to_xrpl()
        self.assertEqual(services.read_memo(tx, "safedeposit/evidence"), data)
        self.assertIsNone(services.read_memo(tx, "other/type"))
        self.assertIsNone(services.read_memo({}, "safedeposit/evidence"))
        with self.assertRaises(LedgerError):
            services._memo("safedeposit/evidence", {"x": "y" * 2000})
