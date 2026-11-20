"""Ledger operations as plain functions (no request objects, no Django models).

Every function that writes to the ledger uses submit_and_wait, returns the
transaction hash and raises LedgerError on any result other than tesSUCCESS.
Seeds and fulfillments are never logged here.
"""
import threading
from collections import defaultdict
from datetime import timezone as dt_timezone
from decimal import Decimal

from xrpl.asyncio.transaction import XRPLReliableSubmissionException
from xrpl.clients import XRPLRequestFailureException
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.models.requests import AccountInfo, AccountLines, Ledger
from xrpl.models.transactions import AccountSet, AccountSetAsfFlag, Payment, TrustSet
from xrpl.transaction import submit_and_wait
from xrpl.utils import ripple_time_to_datetime, xrp_to_drops

from .client import get_client
from .errors import LedgerError

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


def submit(tx, wallet, client=None):
    """Autofill, sign with `wallet`, submit and wait for validation. Returns the validated result dict."""
    client = client or get_client()
    with _lock_for(wallet.address):
        try:
            response = submit_and_wait(tx, client, wallet)
        except (XRPLReliableSubmissionException, XRPLRequestFailureException) as exc:
            raise LedgerError.from_exception(exc) from exc
        except LedgerError:
            raise
        except Exception as exc:
            raise LedgerError.from_exception(exc) from exc
    result = response.result
    code = (result.get("meta") or {}).get("TransactionResult")
    if code != "tesSUCCESS":
        raise LedgerError(code)
    return result


def ledger_close_time(client=None):
    """Close time of the latest validated ledger, as an aware UTC datetime.

    All deadline logic compares against this, never the local clock.
    """
    result = _request(Ledger(ledger_index="validated"), client)
    return ripple_time_to_datetime(result["ledger"]["close_time"]).replace(tzinfo=dt_timezone.utc)


def latest_validated_ledger_index(client=None):
    return int(_request(Ledger(ledger_index="validated"), client)["ledger_index"])


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
