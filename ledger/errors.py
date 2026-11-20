"""Ledger errors with messages a non-technical user can act on."""
import re

FRIENDLY = {
    "tecNO_PERMISSION": "This account isn't allowed to do that right now (wrong party, or too early or too late for this escrow).",
    "tecNO_LINE": "The receiving account has no trust line for this token yet. Add the trust line and try again.",
    "tecUNFUNDED_PAYMENT": "The sending account doesn't hold enough to make this payment.",
    "tecNO_DST": "The destination account doesn't exist on the ledger yet. It needs to be funded with XRP first.",
    "tecCRYPTOCONDITION_ERROR": "The release secret doesn't match this escrow's condition.",
    "tefBAD_QUORUM": "Not enough approvals: two of the three parties must sign.",
    "tefMAX_LEDGER": "The prepared transaction expired before it was submitted. It has to be prepared and signed again.",
    "terPRE_SEQ": "Transactions were sent out of order. Wait a few seconds and try again.",
    "temDISABLED": "This feature is disabled on the current network. Set XRPL_NETWORK=devnet and rerun bootstrap_demo.",
    # Extras that show up in practice.
    "tecUNFUNDED": "The account doesn't hold enough to do this.",
    "tecINSUFFICIENT_RESERVE": "The account needs more XRP to cover the ledger's reserve for this object.",
    "tecNO_TARGET": "That escrow no longer exists on the ledger.",
    "tecNO_ENTRY": "That ledger object doesn't exist.",
    "tecPATH_DRY": "The payment couldn't be delivered (missing trust line or not enough tokens).",
    "tefPAST_SEQ": "This transaction was already sent. Refresh and check the latest state.",
    "actNotFound": "That account doesn't exist on the ledger yet.",
    "tefMASTER_DISABLED": "This account's own key is switched off; only its signers can move funds.",
    "tefBAD_SIGNATURE": "One of the approvals doesn't come from a party named on this deposit.",
    "tefNOT_MULTI_SIGNING": "This account has no signer list, so approvals can't be combined.",
    "tecNO_AUTH": "The receiving account isn't authorised to hold this token.",
    "tecFROZEN": "This token is frozen by its issuer.",
}

_ENGINE_RESULT = re.compile(r"\b(te[cfmlrs][A-Z][A-Z0-9_]+)\b")


class LedgerError(Exception):
    """Any ledger result other than tesSUCCESS. `engine_result` is the raw code, `message` is friendly."""

    def __init__(self, engine_result, message=None):
        self.engine_result = engine_result or "unknown"
        self.message = message or FRIENDLY.get(self.engine_result) or f"The ledger rejected the transaction ({self.engine_result})."
        super().__init__(f"{self.engine_result}: {self.message}")

    @classmethod
    def from_exception(cls, exc):
        """Build from an xrpl-py exception, pulling the engine result out of its text if there is one."""
        text = str(exc)
        match = _ENGINE_RESULT.search(text)
        if match:
            return cls(match.group(1))
        return cls("network_error", f"Couldn't complete the ledger request: {text[:200]}")


class ExpiredPreparedTx(LedgerError):
    """A prepared (multisigned) transaction passed its LastLedgerSequence: prepare and sign it again."""

    def __init__(self, message=None):
        super().__init__("tefMAX_LEDGER", message)
