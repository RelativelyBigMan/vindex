"""Ledger operations as plain functions (no request objects, no Django models).

Every function that writes to the ledger uses submit_and_wait, returns the
transaction hash and raises LedgerError on any result other than tesSUCCESS.
Seeds and fulfillments are never logged here.
"""
import json
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from xrpl.core import binarycodec, keypairs
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.models.requests import AccountInfo, AccountLines, AccountObjects, AccountObjectType, Ledger, LedgerEntry, Tx
from xrpl.models.requests.ledger_entry import Escrow as EscrowLocator
from xrpl.models.transactions import (
    AccountSet,
    AccountSetAsfFlag,
    EscrowCancel,
    EscrowCreate,
    EscrowFinish,
    Memo,
    Payment,
    SignerEntry,
    SignerListSet,
    TrustSet,
)
from xrpl.models.transactions.transaction import Transaction
from xrpl.transaction import autofill, autofill_and_sign, multisign
from xrpl.transaction import sign as _sign
from xrpl.transaction import submit as _submit_blob
from xrpl.utils import datetime_to_ripple_time, ripple_time_to_datetime, xrp_to_drops
from xrpl.wallet import Wallet, generate_faucet_wallet

from .client import get_client
from .errors import ExpiredPreparedTx, LedgerError

# AccountRoot flags (https://xrpl.org/docs/references/protocol/ledger-data/ledger-entry-types/accountroot).
LSF_DISABLE_MASTER = 0x00100000
LSF_DEFAULT_RIPPLE = 0x00800000
LSF_ALLOW_TRUSTLINE_LOCKING = 0x40000000  # lets this issuer's tokens be held in escrow (XLS-85)

DEFAULT_TRUST_LIMIT = "1000000000"

# One lock per sending address: two threads sending from the same account would
# otherwise pick the same Sequence and one of them would fail.
_locks = defaultdict(threading.Lock)
_locks_guard = threading.Lock()


def _lock_for(address):
    with _locks_guard:
        return _locks[address]


def _request(request, client=None):
    """Send a read request; raise LedgerError on an error response."""
    client = client or get_client()
    try:
        response = client.request(request)
    except Exception as exc:  # network trouble, bad JSON, timeouts
        raise LedgerError.from_exception(exc) from exc
    if not response.is_successful():
        code = response.result.get("error", "unknown")
        raise LedgerError(code, None if code == "actNotFound" else response.result.get("error_message"))
    return response.result


# Preliminary results after which a transaction can still end up in a validated ledger.
_TEF_MAY_STILL_APPLY = {"tefPAST_SEQ", "tefALREADY"}


def _send_signed(signed_tx, client):
    """Submit an already signed transaction and wait until it is in a validated ledger.

    Unlike xrpl-py's submit_and_wait this fails at once on a final preliminary result
    (tem*, tef*, tel*), instead of polling until LastLedgerSequence. That matters for
    multisigned transactions, whose LastLedgerSequence is about an hour away.
    -> the validated `tx` result dict (hash, tx_json, meta)
    """
    tx_hash = signed_tx.get_hash()
    last_ledger = signed_tx.last_ledger_sequence
    try:
        prelim = _submit_blob(signed_tx, client).result
    except Exception as exc:
        raise LedgerError.from_exception(exc) from exc
    code = prelim.get("engine_result", "unknown")
    if code == "tefMAX_LEDGER":
        raise ExpiredPreparedTx()
    if code[:3] in ("tem", "tel") or (code[:3] == "tef" and code not in _TEF_MAY_STILL_APPLY):
        raise LedgerError(code)

    while True:
        time.sleep(1)
        response = client.request(Tx(transaction=tx_hash))
        result = response.result
        if response.is_successful() and result.get("validated"):
            final = (result.get("meta") or {}).get("TransactionResult")
            if final != "tesSUCCESS":
                raise LedgerError(final)
            return result
        if not response.is_successful() and result.get("error") != "txnNotFound":
            raise LedgerError(result.get("error", "unknown"), result.get("error_message"))
        if last_ledger is not None and latest_validated_ledger_index(client) > last_ledger:
            if code == "tesSUCCESS" or code[:3] == "ter":
                raise ExpiredPreparedTx("The transaction wasn't included in a ledger in time. Try again.")
            raise LedgerError(code)


def submit(tx, wallet, client=None):
    """Autofill, sign with `wallet`, submit and wait for validation. Returns the validated result dict.

    Holds a per-address lock so two threads never build a transaction with the same Sequence.
    """
    client = client or get_client()
    with _lock_for(wallet.address):
        try:
            signed = autofill_and_sign(tx, client, wallet)
            return _send_signed(signed, client)
        except LedgerError:
            raise
        except Exception as exc:  # network trouble, model validation, timeouts
            raise LedgerError.from_exception(exc) from exc


def ledger_close_time(client=None):
    """Close time of the latest validated ledger, as an aware UTC datetime.

    All deadline logic compares against this, never the local clock.
    """
    result = _request(Ledger(ledger_index="validated"), client)
    return ripple_time_to_datetime(result["ledger"]["close_time"]).replace(tzinfo=dt_timezone.utc)


def latest_validated_ledger_index(client=None):
    return int(_request(Ledger(ledger_index="validated"), client)["ledger_index"])


def get_transaction(tx_hash, client=None):
    """Look a transaction up by hash. -> {'hash', 'validated', 'result', 'tx'}, or None when the network doesn't know it.

    'result' is the engine result (e.g. tesSUCCESS) and 'tx' the transaction's XRPL JSON fields.
    """
    client = client or get_client()
    try:
        response = client.request(Tx(transaction=tx_hash))
    except Exception as exc:
        raise LedgerError.from_exception(exc) from exc
    result = response.result
    if not response.is_successful():
        if result.get("error") == "txnNotFound":
            return None
        raise LedgerError(result.get("error", "unknown"), result.get("error_message"))
    return {
        "hash": result.get("hash", tx_hash),
        "validated": bool(result.get("validated")),
        "result": (result.get("meta") or {}).get("TransactionResult"),
        "tx": result.get("tx_json") or result,
    }


def account_exists(address, client=None):
    try:
        _request(AccountInfo(account=address, ledger_index="validated"), client)
        return True
    except LedgerError as exc:
        if exc.engine_result == "actNotFound":
            return False
        raise


def fund_xrp(from_wallet, to_address, xrp):
    """Send `xrp` XRP from from_wallet to to_address (creates the account if it is new). -> tx hash"""
    tx = Payment(account=from_wallet.address, destination=to_address, amount=xrp_to_drops(Decimal(str(xrp))))
    return submit(tx, from_wallet)["hash"]


def faucet_top_up(wallet, client=None):
    """Ask the test network's faucet to send XRP to an existing wallet. Raises LedgerError when it refuses (rate limit)."""
    for attempt in range(1, 4):
        try:
            generate_faucet_wallet(client or get_client(), wallet=wallet)
            return
        except Exception as exc:
            if attempt == 3:
                raise LedgerError("faucet_unavailable", "The test network's faucet is busy. Wait a minute and try again.") from exc
            time.sleep(2 * attempt)


def get_account_flags(address, client=None):
    """-> {'default_ripple', 'allow_trustline_locking', 'disable_master'} booleans, read from the validated ledger."""
    data = _request(AccountInfo(account=address, ledger_index="validated"), client)["account_data"]
    flags = int(data.get("Flags", 0))
    return {
        "default_ripple": bool(flags & LSF_DEFAULT_RIPPLE),
        "allow_trustline_locking": bool(flags & LSF_ALLOW_TRUSTLINE_LOCKING),
        "disable_master": bool(flags & LSF_DISABLE_MASTER),
    }


def set_issuer_flags(issuer_wallet):
    """Prepare a token issuer: DefaultRipple FIRST, then Allow Trust Line Locking.

    Must run before any trust line to this issuer exists (lines created before
    DefaultRipple break holder-to-holder payments). Skips flags already set and
    verifies both on the ledger. -> list of tx hashes (empty if nothing to do)
    """
    hashes = []
    flags = get_account_flags(issuer_wallet.address)
    steps = (
        ("default_ripple", AccountSetAsfFlag.ASF_DEFAULT_RIPPLE),
        ("allow_trustline_locking", AccountSetAsfFlag.ASF_ALLOW_TRUSTLINE_LOCKING),
    )
    for name, asf in steps:
        if not flags[name]:
            hashes.append(submit(AccountSet(account=issuer_wallet.address, set_flag=asf), issuer_wallet)["hash"])
    flags = get_account_flags(issuer_wallet.address)
    missing = [name for name, _ in steps if not flags[name]]
    if missing:
        raise LedgerError("flags_not_set", f"Issuer {issuer_wallet.address} is missing flags: {', '.join(missing)}.")
    return hashes


def _lines(address, client=None, peer=None):
    """All trust lines of an account (follows pagination)."""
    lines, marker = [], None
    while True:
        result = _request(AccountLines(account=address, peer=peer, ledger_index="validated", marker=marker), client)
        lines.extend(result.get("lines", []))
        marker = result.get("marker")
        if not marker:
            return lines


def has_trust_line(address, currency_code, issuer_address, client=None):
    return any(line["currency"] == currency_code for line in _lines(address, client, peer=issuer_address))


def ensure_trust_line(holder_wallet, currency_code, issuer_address, limit=DEFAULT_TRUST_LIMIT):
    """Create the holder's trust line to the issuer. No-op (returns None) if it already exists. -> tx hash | None"""
    if has_trust_line(holder_wallet.address, currency_code, issuer_address):
        return None
    tx = TrustSet(
        account=holder_wallet.address,
        limit_amount=IssuedCurrencyAmount(currency=currency_code, issuer=issuer_address, value=str(limit)),
    )
    return submit(tx, holder_wallet)["hash"]


def autofill_trust_set(address, currency_code, issuer_address, limit=DEFAULT_TRUST_LIMIT):
    """Unsigned, autofilled TrustSet as XRPL JSON, for a wallet to sign."""
    client = get_client()
    tx = TrustSet(account=address, limit_amount=IssuedCurrencyAmount(currency=currency_code, issuer=issuer_address, value=limit))
    try:
        return autofill(tx, client).to_xrpl()
    except Exception as exc:
        raise LedgerError.from_exception(exc) from exc


def issue_tokens(issuer_wallet, to_address, currency_code, amount):
    """The issuer pays `amount` of its token to to_address (which needs a trust line). -> tx hash"""
    tx = Payment(
        account=issuer_wallet.address,
        destination=to_address,
        amount=IssuedCurrencyAmount(currency=currency_code, issuer=issuer_wallet.address, value=str(amount)),
    )
    return submit(tx, issuer_wallet)["hash"]


def get_balances(address, client=None):
    """-> {'XRP': Decimal, '<CODE>': Decimal, ...} from account_info and account_lines.

    An account that doesn't exist yet returns {'XRP': Decimal('0')}.
    Token balances with the same code from different issuers are added together.
    """
    client = client or get_client()
    try:
        data = _request(AccountInfo(account=address, ledger_index="validated"), client)["account_data"]
    except LedgerError as exc:
        if exc.engine_result == "actNotFound":
            return {"XRP": Decimal("0")}
        raise
    balances = {"XRP": Decimal(data["Balance"]) / Decimal(1_000_000)}
    for line in _lines(address, client):
        balances[line["currency"]] = balances.get(line["currency"], Decimal("0")) + Decimal(line["balance"])
    return balances


# --------------------------------------------------------------------------------------
# Patch 2: settlement account, token escrow, multisigned payouts, memo anchoring
# --------------------------------------------------------------------------------------

SETTLEMENT_FUND_XRP = Decimal("5")
SIGNER_QUORUM = 2
CANCEL_SAFETY = timedelta(seconds=10)  # wait this long past CancelAfter before cancelling
MULTISIG_LEDGER_WINDOW = 1000  # ledgers a prepared payout stays valid (about 1 hour)
WALLET_LEDGER_WINDOW = 100  # ledgers an unsigned wallet transaction stays valid (about 5 minutes)
MAX_TOKEN_DIGITS = 15  # significant digits an issued-currency amount can carry exactly
MAX_MEMO_BYTES = 900  # the ledger caps all memos of a transaction at 1 kB


def _aware(dt):
    """Deadlines must be timezone-aware; a naive datetime would silently use the server's zone."""
    if not isinstance(dt, datetime) or dt.tzinfo is None:
        raise LedgerError("bad_deadline", "Deadlines must be timezone-aware datetimes.")
    return dt


def _decimal(value):
    """Exact Decimal from str / int / Decimal. Floats are refused: they aren't exact money."""
    if isinstance(value, bool) or isinstance(value, float):
        raise LedgerError("bad_amount", "Amounts must be given as text or Decimal, not floating point numbers.")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LedgerError("bad_amount", f"{value!r} is not a valid amount.") from exc
    if not amount.is_finite():
        raise LedgerError("bad_amount", f"{value!r} is not a valid amount.")
    return amount


def token_value(amount):
    """Decimal -> the plain string the ledger expects ('1000', '0.5'), refusing amounts it can't hold exactly."""
    amount = _decimal(amount).normalize()
    if len(amount.as_tuple().digits) > MAX_TOKEN_DIGITS:
        raise LedgerError("bad_amount", f"{amount} has more than {MAX_TOKEN_DIGITS} significant digits.")
    return format(amount, "f")


def get_token_balance(address, currency_code, issuer_address, client=None):
    """Live balance of one specific token (code + issuer) held by `address`. Decimal('0') without a trust line."""
    total = Decimal("0")
    for line in _lines(address, client, peer=issuer_address):
        if line["currency"] == currency_code:
            total += Decimal(line["balance"])
    return total


def get_signer_list(address, client=None):
    """-> {'quorum': int, 'signers': [{'address', 'weight'}]} from the validated ledger, or None if there is none."""
    result = _request(AccountInfo(account=address, ledger_index="validated", signer_lists=True), client)
    lists = result.get("signer_lists") or result.get("account_data", {}).get("signer_lists") or []
    if not lists:
        return None
    entries = [e["SignerEntry"] for e in lists[0].get("SignerEntries", [])]
    return {
        "quorum": int(lists[0]["SignerQuorum"]),
        "signers": [{"address": e["Account"], "weight": int(e["SignerWeight"])} for e in entries],
    }


def create_settlement_account(treasury_wallet, signer_addresses, currency_code, issuer_address, on_step=None):
    """Create a fresh 2-of-3 settlement account for one lease. -> (address, seed)

    Steps, each a validated transaction: fund 5 XRP from the treasury, TrustSet to the
    issuer, SignerListSet (three signers of weight 1, quorum 2), AccountSet asfDisableMaster.
    After each one `on_step(step, tx_hash)` is called with step in
    'funded' | 'trust_line' | 'signers' | 'master_disabled'.

    Finally the ledger itself is checked: master key disabled, signer list = exactly these
    three addresses with weight 1 and quorum 2, trust line present. The returned seed can no
    longer sign anything; it is kept only for reference on Testnet.
    """
    signers = list(signer_addresses)
    if len(signers) != 3 or len(set(signers)) != 3:
        raise LedgerError("bad_signers", "A settlement account needs three different signers: tenant, landlord and arbiter.")
    notify = on_step or (lambda step, tx_hash: None)
    wallet = Wallet.create()
    address = wallet.address

    notify("funded", fund_xrp(treasury_wallet, address, SETTLEMENT_FUND_XRP))
    notify("trust_line", ensure_trust_line(wallet, currency_code, issuer_address))
    signer_tx = SignerListSet(
        account=address,
        signer_quorum=SIGNER_QUORUM,
        signer_entries=[SignerEntry(account=a, signer_weight=1) for a in signers],
    )
    notify("signers", submit(signer_tx, wallet)["hash"])
    disable_tx = AccountSet(account=address, set_flag=AccountSetAsfFlag.ASF_DISABLE_MASTER)
    notify("master_disabled", submit(disable_tx, wallet)["hash"])

    problems = verify_settlement_account(address, signers, currency_code, issuer_address)
    if problems:
        raise LedgerError("settlement_not_verified", "The settlement account isn't set up correctly: " + "; ".join(problems))
    return address, wallet.seed


def verify_settlement_account(address, signer_addresses, currency_code, issuer_address, client=None):
    """Check a settlement account on the validated ledger. -> list of problems (empty when all is right)."""
    client = client or get_client()
    problems = []
    if not get_account_flags(address, client)["disable_master"]:
        problems.append("the master key is still enabled")
    signer_list = get_signer_list(address, client)
    if not signer_list:
        problems.append("there is no signer list")
    else:
        if signer_list["quorum"] != SIGNER_QUORUM:
            problems.append(f"the quorum is {signer_list['quorum']}, not {SIGNER_QUORUM}")
        if sorted(s["address"] for s in signer_list["signers"]) != sorted(signer_addresses):
            problems.append("the signers are not exactly the tenant, landlord and arbiter")
        if any(s["weight"] != 1 for s in signer_list["signers"]):
            problems.append("a signer has a weight other than 1")
    if not has_trust_line(address, currency_code, issuer_address, client):
        problems.append(f"there is no {currency_code} trust line")
    return problems


# ---- escrow ----

def _escrow_create_tx(tenant_address, destination, currency_code, issuer, amount, condition_hex, cancel_after, **extra):
    amount = _decimal(amount)
    if amount <= 0:
        raise LedgerError("bad_amount", "The deposit must be greater than zero.")
    return EscrowCreate(
        account=tenant_address,
        destination=destination,
        amount=IssuedCurrencyAmount(currency=currency_code, issuer=issuer, value=token_value(amount)),
        condition=condition_hex,
        cancel_after=datetime_to_ripple_time(_aware(cancel_after)),
        **extra,
    )


def _require_future(cancel_after, client):
    if ledger_close_time(client) >= _aware(cancel_after):
        raise LedgerError("deadline_passed", "The automatic return date has already passed, so the deposit can't be locked.")


def build_escrow_create(tenant_address, destination, currency_code, issuer, amount, condition_hex, cancel_after):
    """Unsigned, autofilled EscrowCreate as XRPL JSON, for a browser wallet to sign (Patch 5).

    Amount is the issued token; there is no FinishAfter. LastLedgerSequence leaves about
    5 minutes for the person to approve it in their wallet.
    """
    client = get_client()
    _require_future(cancel_after, client)
    tx = _escrow_create_tx(
        tenant_address, destination, currency_code, issuer, amount, condition_hex, cancel_after,
        last_ledger_sequence=latest_validated_ledger_index(client) + WALLET_LEDGER_WINDOW,
    )
    return autofill(tx, client).to_xrpl()


def create_escrow(tenant_wallet, destination, currency_code, issuer, amount, condition_hex, cancel_after):
    """The tenant locks the deposit in a conditional token escrow. -> (tx_hash, offer_sequence)

    offer_sequence is the Sequence of the validated EscrowCreate; with the owner address it identifies the escrow.
    """
    client = get_client()
    _require_future(cancel_after, client)
    tx = _escrow_create_tx(tenant_wallet.address, destination, currency_code, issuer, amount, condition_hex, cancel_after)
    result = submit(tx, tenant_wallet, client)
    return result["hash"], int(result["tx_json"]["Sequence"])


def get_escrow(owner, offer_sequence, client=None):
    """Look the escrow up on the validated ledger. -> dict, or None when it doesn't exist (finished or cancelled).

    Keys: owner, destination, amount (Decimal), currency, issuer, condition, cancel_after (aware UTC or None), raw.
    """
    client = client or get_client()
    request = LedgerEntry(escrow=EscrowLocator(owner=owner, seq=int(offer_sequence)), ledger_index="validated")
    try:
        response = client.request(request)
    except Exception as exc:
        raise LedgerError.from_exception(exc) from exc
    if not response.is_successful():
        if response.result.get("error") == "entryNotFound":
            return None
        raise LedgerError(response.result.get("error", "unknown"), response.result.get("error_message"))
    node = response.result["node"]
    amount = node["Amount"]
    is_token = isinstance(amount, dict)
    cancel = node.get("CancelAfter")
    return {
        "owner": node["Account"],
        "destination": node["Destination"],
        "amount": Decimal(amount["value"]) if is_token else Decimal(amount) / Decimal(1_000_000),
        "currency": amount["currency"] if is_token else "XRP",
        "issuer": amount.get("issuer") if is_token else None,
        "condition": node.get("Condition"),
        "cancel_after": ripple_time_to_datetime(cancel).replace(tzinfo=dt_timezone.utc) if cancel is not None else None,
        "raw": node,
    }


def find_escrow_create_hash(owner, destination, condition_hex, client=None):
    """Hash of the EscrowCreate behind an escrow `owner` holds for `destination` under `condition_hex`, or None.

    Used to recover when an escrow was created but its confirmation never reached us (network failure).
    """
    marker = None
    while True:
        request = AccountObjects(account=owner, type=AccountObjectType.ESCROW, ledger_index="validated", marker=marker)
        result = _request(request, client)
        for node in result.get("account_objects", []):
            if node.get("Destination") == destination and (node.get("Condition") or "").upper() == condition_hex.upper():
                return node.get("PreviousTxnID")
        marker = result.get("marker")
        if not marker:
            return None


def _require_before_deadline(cancel_after, client=None):
    """EscrowFinish only works before CancelAfter. Compared against the ledger's clock, not ours."""
    if ledger_close_time(client) >= _aware(cancel_after):
        raise LedgerError(
            "escrow_expired",
            "It's too late to release this deposit: the automatic return date has passed, "
            "so it goes back to the tenant automatically.",
        )


def _escrow_cancel_after(owner, offer_sequence, client):
    escrow = get_escrow(owner, offer_sequence, client)
    if escrow is None:
        raise LedgerError("tecNO_TARGET")
    return escrow["cancel_after"]


def build_escrow_finish(submitter_address, owner, offer_sequence, condition_hex, fulfillment_hex, cancel_after=None):
    """Unsigned, autofilled EscrowFinish as XRPL JSON, for a browser wallet to sign (Patch 5).

    The result CONTAINS THE FULFILLMENT (the release secret): hand it only to the tenant or
    landlord and never log it. The autofilled fee already covers the fulfillment's extra cost.
    Refused when the ledger close time has reached CancelAfter (read from the ledger if not given).
    """
    client = get_client()
    cancel_after = cancel_after or _escrow_cancel_after(owner, offer_sequence, client)
    if cancel_after is not None:
        _require_before_deadline(cancel_after, client)
    tx = EscrowFinish(
        account=submitter_address, owner=owner, offer_sequence=int(offer_sequence),
        condition=condition_hex, fulfillment=fulfillment_hex,
        last_ledger_sequence=latest_validated_ledger_index(client) + WALLET_LEDGER_WINDOW,
    )
    return autofill(tx, client).to_xrpl()


def finish_escrow(submitter_wallet, owner, offer_sequence, condition_hex, fulfillment_hex, cancel_after=None):
    """Release the escrow to its destination (the settlement account). -> tx_hash

    Any account may submit; the fulfillment is what authorises it. Refused BEFORE submitting
    when ledger_close_time() >= cancel_after (read from the ledger if not given).
    """
    client = get_client()
    cancel_after = cancel_after or _escrow_cancel_after(owner, offer_sequence, client)
    if cancel_after is not None:
        _require_before_deadline(cancel_after, client)
    tx = EscrowFinish(
        account=submitter_wallet.address, owner=owner, offer_sequence=int(offer_sequence),
        condition=condition_hex, fulfillment=fulfillment_hex,
    )
    return submit(tx, submitter_wallet, client)["hash"]


def cancel_escrow(submitter_wallet, owner, offer_sequence, cancel_after):
    """Return an expired escrow to the tenant (any account may submit; the bot does). -> tx_hash

    Refused BEFORE submitting unless ledger_close_time() > cancel_after + 10 s.
    """
    client = get_client()
    if not ledger_close_time(client) > _aware(cancel_after) + CANCEL_SAFETY:
        raise LedgerError(
            "escrow_not_expired",
            "The deposit can't be returned automatically yet: the lease's return date (plus a 10 second margin) hasn't passed on the ledger.",
        )
    tx = EscrowCancel(account=submitter_wallet.address, owner=owner, offer_sequence=int(offer_sequence))
    return submit(tx, submitter_wallet, client)["hash"]


# ---- multisigned payouts from the settlement account ----

def validate_payouts(payouts, balance):
    """Pure check of a payout plan against the settlement balance. -> [(destination, Decimal)] without zero amounts.

    Refuses negative or inexact amounts, an empty plan, and any plan whose amounts don't add
    up EXACTLY (Decimal) to `balance`, so nothing can be left stranded in the settlement account.
    """
    balance = _decimal(balance)
    cleaned, total = [], Decimal("0")
    for destination, raw in payouts:
        amount = _decimal(raw)
        if amount < 0:
            raise LedgerError("bad_amount", "Payout amounts can't be negative.")
        token_value(amount)  # refuses amounts the ledger can't hold exactly
        total += amount
        if amount > 0:
            cleaned.append((destination, amount))
    if total != balance:
        raise LedgerError(
            "payout_sum_mismatch",
            f"The payouts add up to {format(total, 'f')}, but the settlement account holds {format(balance, 'f')}. They must match exactly.",
        )
    if not cleaned:
        raise LedgerError("nothing_to_pay", "There is nothing to pay out: the settlement account is empty.")
    return cleaned


def prepare_payout_txs(settlement_address, payouts, currency_code, issuer):
    """Build the unsigned payout Payments for 2-of-3 signing. -> list of XRPL JSON tx dicts, in submit order

    payouts = [(destination, amount), ...]. Zero amounts are skipped; the rest must add up
    exactly to the account's live token balance. Each transaction gets an explicit, consecutive
    Sequence (from account_info), LastLedgerSequence = latest validated + 1000 so signatures
    can arrive minutes apart, and a fee autofilled for 2 signers.
    """
    client = get_client()
    balance = get_token_balance(settlement_address, currency_code, issuer, client)
    cleaned = validate_payouts(payouts, balance)
    account = _request(AccountInfo(account=settlement_address, ledger_index="validated"), client)["account_data"]
    sequence = int(account["Sequence"])
    last_ledger = latest_validated_ledger_index(client) + MULTISIG_LEDGER_WINDOW
    prepared = []
    for offset, (destination, amount) in enumerate(cleaned):
        if destination == settlement_address:
            raise LedgerError("bad_destination", "A payout can't go back to the settlement account.")
        tx = Payment(
            account=settlement_address,
            destination=destination,
            amount=IssuedCurrencyAmount(currency=currency_code, issuer=issuer, value=token_value(amount)),
            sequence=sequence + offset,
            last_ledger_sequence=last_ledger,
            signing_pub_key="",
        )
        try:
            prepared.append(autofill(tx, client, signers_count=SIGNER_QUORUM).to_xrpl())
        except Exception as exc:
            raise LedgerError.from_exception(exc) from exc
    return prepared


def _without_signatures(tx_json):
    """Canonical form of a tx dict with every signature removed, for comparing what was signed."""
    body = Transaction.from_xrpl({k: v for k, v in tx_json.items() if k not in ("Signers", "TxnSignature")}).to_xrpl()
    body["SigningPubKey"] = ""
    return body


def sign_prepared(tx_dict, wallet):
    """One signer's multisignature over a prepared transaction. -> signed XRPL JSON (the tx plus one Signer)."""
    return _sign(Transaction.from_xrpl(_without_signatures(tx_dict)), wallet, multisign=True).to_xrpl()


def execute_multisigned(tx_dict, signed_jsons, enforce_quorum=True):
    """Combine the signatures, submit and wait for validation. -> tx_hash

    Before submitting: every signature must be over exactly `tx_dict`, and (unless
    enforce_quorum=False, used only by the smoke test to prove the ledger refuses it too)
    at least 2 different signers are required, else LedgerError('tefBAD_QUORUM').
    Raises ExpiredPreparedTx when the transaction is past its LastLedgerSequence
    (tefMAX_LEDGER): prepare and sign again.
    """
    client = get_client()
    body = _without_signatures(tx_dict)
    signed_txs, seen = [], set()
    for signed in signed_jsons:
        if _without_signatures(signed) != body or len(signed.get("Signers", [])) != 1:
            raise LedgerError("signature_mismatch", "An approval was given for a different transaction. It has to be signed again.")
        signer = signed["Signers"][0]["Signer"]["Account"]
        if signer not in seen:
            seen.add(signer)
            signed_txs.append(Transaction.from_xrpl(signed))
    if (enforce_quorum and len(seen) < SIGNER_QUORUM) or not seen:
        raise LedgerError("tefBAD_QUORUM")
    last_ledger = body.get("LastLedgerSequence")
    if last_ledger is not None and latest_validated_ledger_index(client) >= int(last_ledger):
        raise ExpiredPreparedTx()
    combined = multisign(Transaction.from_xrpl(body), signed_txs)
    with _lock_for(body["Account"]):
        try:
            return _send_signed(combined, client)["hash"]
        except LedgerError:
            raise
        except Exception as exc:
            raise LedgerError.from_exception(exc) from exc


def verify_multisig_blob(tx_dict, blob_hex, signer_address):
    """Check a wallet's multisignature (a signed tx blob) over exactly `tx_dict`. -> signed XRPL JSON

    The blob must carry exactly one Signer: `signer_address`, using its own key, with a valid
    signature over this transaction. Raises LedgerError('signature_mismatch') otherwise.
    """
    def refuse(message):
        return LedgerError("signature_mismatch", message)

    try:
        signed = binarycodec.decode(str(blob_hex))
    except Exception as exc:
        raise refuse("The wallet returned something that isn't a signed transaction.") from exc
    signers = signed.get("Signers") or []
    if len(signers) != 1:
        raise refuse("Expected exactly one signature from the wallet.")
    signer = signers[0].get("Signer") or {}
    if signer.get("Account") != signer_address:
        raise refuse("The approval was signed by a different account.")
    body = _without_signatures(tx_dict)
    try:
        same = _without_signatures(signed) == body
    except Exception:
        same = False
    if not same:
        raise refuse("The wallet signed a different transaction than the one prepared.")
    public_key = signer.get("SigningPubKey") or ""
    try:
        own_key = keypairs.derive_classic_address(public_key) == signer_address
        message = bytes.fromhex(binarycodec.encode_for_multisigning(body, signer_address))
        valid = own_key and keypairs.is_valid_message(message, bytes.fromhex(signer.get("TxnSignature") or ""), public_key)
    except Exception:
        valid = False
    if not valid:
        raise refuse("The approval's signature isn't valid for this account's own key.")
    return signed


# ---- memo anchoring (used for evidence in Patch 6) ----

def _memo(memo_type, data_dict):
    data = json.dumps(data_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(data) + len(memo_type.encode("utf-8")) > MAX_MEMO_BYTES:
        raise LedgerError("memo_too_large", "That is too much data to anchor in one transaction.")
    return Memo(
        memo_type=memo_type.encode("utf-8").hex().upper(),
        memo_format=b"application/json".hex().upper(),
        memo_data=data.hex().upper(),
    )


def anchor_memo(wallet, memo_type, data_dict):
    """Write one Memo to the ledger on an AccountSet that changes no flags. -> tx_hash

    memo_type is text such as 'vindex/evidence'; data_dict is stored as compact JSON.
    Memos are PUBLIC and permanent: anchor hashes and ids only, never personal data.
    """
    return submit(AccountSet(account=wallet.address, memos=[_memo(memo_type, data_dict)]), wallet)["hash"]


def build_anchor_memo(address, memo_type, data_dict):
    """The same anchoring transaction, unsigned and autofilled, for a browser wallet to sign."""
    client = get_client()
    tx = AccountSet(
        account=address, memos=[_memo(memo_type, data_dict)],
        last_ledger_sequence=latest_validated_ledger_index(client) + WALLET_LEDGER_WINDOW,
    )
    try:
        return autofill(tx, client).to_xrpl()
    except Exception as exc:
        raise LedgerError.from_exception(exc) from exc


def read_memo(tx_json, memo_type):
    """The JSON data of the first memo of `memo_type` on a transaction's XRPL JSON, or None."""
    wanted = memo_type.encode("utf-8").hex().upper()
    for entry in tx_json.get("Memos") or []:
        memo = entry.get("Memo") or {}
        if (memo.get("MemoType") or "").upper() == wanted:
            try:
                return json.loads(bytes.fromhex(memo.get("MemoData") or "").decode("utf-8"))
            except ValueError:
                return None
    return None


# ---- raw ledger view of an account (the settlement inspector) ----

def get_account_raw(address, client=None):
    """What the validated ledger holds for an account, unprocessed: account root, signer lists, trust lines."""
    result = _request(AccountInfo(account=address, ledger_index="validated", signer_lists=True), client)
    return {
        "ledger_index": result.get("ledger_index"),
        "account_data": result.get("account_data"),
        "signer_lists": result.get("signer_lists") or (result.get("account_data") or {}).get("signer_lists") or [],
        "lines": _lines(address, client),
    }
