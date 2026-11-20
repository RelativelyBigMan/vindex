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
