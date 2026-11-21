"""Xaman sign requests: one per user action, finished when Xaman reports it signed.

Flow: start_*() creates the Xaman payload and a XamanRequest row; the browser polls our API, which
calls refresh(). refresh() asks Xaman for the payload; once signed it runs the finisher for the
purpose (register the validated transaction, store the multisignature, create the account).
"""
import logging
import re
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from xrpl.core.addresscodec import is_valid_classic_address

from ledger import services, xaman
from ledger.errors import LedgerError

from . import lifecycle
from .lifecycle import InvalidInput, InvalidTransition
from .models import Party, XamanRequest

log = logging.getLogger(__name__)

P = XamanRequest.Purpose
R = XamanRequest.Status
VALIDATION_TIMEOUT = timedelta(minutes=3)
TRANSIENT = {"network_error", "unknown"}  # connection problems: retry on the next poll
WALLET_FILLS = ("Sequence", "Fee", "LastLedgerSequence", "SigningPubKey", "TxnSignature")


def _for_wallet(tx):
    """Let Xaman fill sequence, fee and expiry itself (the user may take a minute to sign)."""
    return {k: v for k, v in tx.items() if k not in WALLET_FILLS}


def _start(purpose, txjson, *, party=None, submit=True, multisign=False, instruction="", **links):
    payload = xaman.create_payload(
        txjson, submit=submit, multisign=multisign, instruction=instruction,
        **({"signers": [party.address]} if party is not None and purpose != P.SIGNIN else {}),
        **({"user_token": party.xaman_user_token} if party is not None and party.xaman_user_token else {}),
    )
    return XamanRequest.objects.create(
        xaman_uuid=payload["uuid"], purpose=purpose, party=party, deeplink=payload["deeplink"], pushed=payload["pushed"], **links,
    )


def start_signin():
    payload = xaman.create_payload({"TransactionType": "SignIn"}, submit=False, instruction="Sign in to safeDeposit")
    return XamanRequest.objects.create(xaman_uuid=payload["uuid"], purpose=P.SIGNIN, deeplink=payload["deeplink"], pushed=payload["pushed"])


def start_escrow(lease, party):
    if "lock_deposit" not in lifecycle.allowed_actions(lease, party):
        raise InvalidTransition("Only the tenant can lock the deposit, once the settlement account is ready.", "not_allowed")
    tx = services.build_escrow_create(
        lease.tenant.address, lease.settlement_address, lease.currency.code, lease.currency.issuer_address,
        lease.amount, lease.condition_hex, lease.cancel_after,
    )
    return _start(P.ESCROW_CREATE, _for_wallet(tx), party=party, lease=lease,
                  instruction=f"Lock the {lifecycle.money(lease, lease.amount)} deposit for '{lease.title or 'your lease'}'")


def start_payout(proposal, party):
    index = lifecycle.next_unsigned_index(proposal, party)
    if index is None:
        return None
    lease = proposal.lease
    if f"sign:{proposal.pk}" not in lifecycle.allowed_actions(lease, party):
        raise InvalidTransition("You can't approve this split right now.", "not_allowed")
    count = len(proposal.prepared_txs)
    return _start(
        P.PAYOUT_SIGN, proposal.prepared_txs[index], party=party, submit=False, multisign=True,
        lease=lease, proposal=proposal, payload_index=index,
        instruction=f"Approve payout {index + 1} of {count}: {lifecycle.money(lease, proposal.tenant_amount)} to the tenant, "
                    f"{lifecycle.money(lease, proposal.landlord_amount)} to the landlord",
    )


def start_anchor(item, party):
    if item.uploaded_by_id != party.pk or item.anchor_tx_hash:
        raise InvalidTransition("Only the uploader can anchor a photo, and only once.", "not_allowed")
    tx = services.build_anchor_memo(party.address, lifecycle.EVIDENCE_MEMO_TYPE, lifecycle.evidence_memo(item))
    return _start(P.EVIDENCE_ANCHOR, _for_wallet(tx), party=party, lease=item.lease, evidence=item,
                  instruction="Anchor your photo's fingerprint on the ledger")


def start_trustline(party, currency):
    tx = {"TransactionType": "TrustSet", "Account": party.address,
          "LimitAmount": {"currency": currency.code, "issuer": currency.issuer_address, "value": services.DEFAULT_TRUST_LIMIT}}
    return _start(P.TRUSTSET, tx, party=party, currency_code=currency.code, instruction=f"Allow your account to hold {currency.label}")


# ---- refresh and finish ----

def _timed_out(req):
    return timezone.now() - (req.signed_at or req.created_at) > VALIDATION_TIMEOUT


def refresh(req):
    if req.status == R.OPEN:
        info = xaman.get_payload(req.xaman_uuid)
        if info["status"] == "open":
            return req
        if info["status"] in (R.REJECTED, R.EXPIRED):
            XamanRequest.objects.filter(pk=req.pk, status=R.OPEN).update(status=info["status"])
            req.refresh_from_db()
            return req
        token = info.get("user_token")
        stored = {k: v for k, v in info.items() if k != "user_token"}  # the push token lives on Party only
        won = XamanRequest.objects.filter(pk=req.pk, status=R.OPEN).update(status=R.SIGNED, result=stored, signed_at=timezone.now())
        if won and req.party_id and token:
            Party.objects.filter(pk=req.party_id).update(xaman_user_token=token)
        req.refresh_from_db()
    if req.status != R.SIGNED:
        return req
    if not XamanRequest.objects.filter(pk=req.pk, status=R.SIGNED).update(status=R.FINISHING):
        req.refresh_from_db()  # another poll is finishing it
        return req
    req.status = R.FINISHING
    try:
        finished = FINISHERS[req.purpose](req)
    except LedgerError as exc:
        if exc.engine_result in TRANSIENT and not _timed_out(req):
            XamanRequest.objects.filter(pk=req.pk, status=R.FINISHING).update(status=R.SIGNED)
            req.status = R.SIGNED
            return req
        return _fail(req, exc)
    except (InvalidInput, InvalidTransition) as exc:
        return _fail(req, exc)
    if finished:
        req.status = R.DONE
        req.save(update_fields=["status", "party"])
    else:
        XamanRequest.objects.filter(pk=req.pk, status=R.FINISHING).update(status=R.SIGNED)
        req.status = R.SIGNED
    return req


def _fail(req, exc):
    log.info("Xaman request %s (%s) failed: %s", req.pk, req.purpose, exc)
    req.status, req.error = R.FAILED, exc.message[:300]
    req.save(update_fields=["status", "error"])
    return req


def _validated(req):
    """The signed transaction once it is in a validated ledger; None while still waiting."""
    txid = str(req.result.get("txid") or "").upper()
    if not re.fullmatch(r"[0-9A-F]{64}", txid):
        raise InvalidInput("Xaman didn't report a transaction id.", "no_txid")
    found = services.get_transaction(txid)
    if found is not None and found.get("validated"):
        if found.get("result") != "tesSUCCESS":
            raise InvalidInput(f"The transaction failed on the ledger ({found.get('result')}).", "tx_failed")
        return txid
    if _timed_out(req):
        raise InvalidInput("The network never validated the transaction. Try again.", "not_validated")
    return None


def _finish_signin(req):
    address = str(req.result.get("account") or "")
    if not is_valid_classic_address(address):
        raise InvalidInput("Xaman didn't report a valid account.", "bad_address")
    party, _ = Party.objects.get_or_create(address=address)
    token = xaman.get_payload(req.xaman_uuid).get("user_token")  # not kept in req.result
    if token and party.xaman_user_token != token:
        party.xaman_user_token = token
        party.save(update_fields=["xaman_user_token"])
    req.party = party
    return True


def _finish_escrow(req):
    txid = _validated(req)
    if txid is None:
        return False
    lifecycle.register_wallet_escrow(req.lease, txid)
    return True


def _finish_payout(req):
    lifecycle.add_signature(req.proposal, req.party, req.payload_index, req.result.get("hex") or "")
    return True


def _finish_anchor(req):
    txid = _validated(req)
    if txid is None:
        return False
    lifecycle.register_wallet_anchor(req.evidence, txid)
    return True


def _finish_trustline(req):
    if _validated(req) is None:
        return False
    cache.delete(f"balances:{req.party.address}")
    return True


FINISHERS = {
    P.SIGNIN: _finish_signin,
    P.ESCROW_CREATE: _finish_escrow,
    P.PAYOUT_SIGN: _finish_payout,
    P.EVIDENCE_ANCHOR: _finish_anchor,
    P.TRUSTSET: _finish_trustline,
}


def present(req):
    return {
        "request_id": str(req.pk), "purpose": req.purpose, "status": req.status, "error": req.error,
        "qr": xaman.qr_data_uri(req.deeplink) if req.deeplink and req.status == R.OPEN else "",
        "deeplink": req.deeplink if req.status == R.OPEN else "", "pushed": req.pushed,
    }
