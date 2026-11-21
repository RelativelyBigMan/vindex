"""Lease lifecycle: every status transition, each one guarded.

    PENDING_ACCEPTANCE -> DRAFT -> SETTLEMENT_READY -> ACTIVE -> RETURNED
                                                       ACTIVE -> IN_SETTLEMENT -> SETTLED
    FAILED on setup errors; CANCELLED before the deposit is locked.

Rules followed throughout:
- The ledger is the source of truth. A transition first succeeds on the ledger, then the
  database is moved with a conditional update (status must still be the expected one).
- Deadlines are compared with the ledger's close time, never the local clock.
- One action at a time per lease in this process (a double click must not lock a deposit twice).
- Every ledger action writes a LedgerEvent. The fulfillment and seeds are never logged.

InvalidTransition -> HTTP 409, InvalidInput -> HTTP 400 (see core/api.py).
"""
import logging
import re
import threading
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.db import connection
from django.utils import timezone
from xrpl.utils import datetime_to_ripple_time
from xrpl.wallet import Wallet

from ledger import services
from ledger.errors import ExpiredPreparedTx, LedgerError

from .models import Evidence, Lease, LedgerEvent, PlatformWallet, SettlementProposal

log = logging.getLogger(__name__)

S = Lease.Status
QUORUM = services.SIGNER_QUORUM
LIVE_TTL = 10  # seconds a live ledger reading may be reused

SETUP_SUMMARIES = {
    "funded": "Settlement account created and funded",
    "trust_line": "Settlement account can hold {code} (trust line set)",
    "signers": "Tenant, landlord and arbiter set as signers (2 of 3 required)",
    "master_disabled": "Settlement account's own key disabled: only 2 of 3 signatures can move funds",
}


class InvalidTransition(Exception):
    """The action isn't allowed in the lease's current state, or for this party."""

    def __init__(self, message, code="invalid_transition"):
        self.message, self.code = message, code
        super().__init__(message)


class InvalidInput(Exception):
    """The request itself is wrong (bad amount, unknown reason, ...)."""

    def __init__(self, message, code="bad_request"):
        self.message, self.code = message, code
        super().__init__(message)


# ---- helpers ----

def plain(amount):
    """Decimal -> '1000' / '0.5' (no exponent, no trailing zeros)."""
    return format(Decimal(amount).normalize(), "f")


def money(lease, amount):
    return f"{Decimal(amount).normalize():,f} {lease.currency.code}"


def parse_amount(value, name="amount"):
    """Exact, finite, non-negative Decimal from JSON input (text or number)."""
    if isinstance(value, bool) or value is None or value == "":
        raise InvalidInput(f"Give {name} as a number.", "bad_amount")
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise InvalidInput(f"{value!r} is not a valid amount.", "bad_amount") from exc
    if not amount.is_finite() or amount < 0:
        raise InvalidInput("Amounts can't be negative.", "bad_amount")
    if amount != amount.quantize(Decimal("0.000001")) or amount >= Decimal("1e12"):
        raise InvalidInput("Amounts can have at most 6 decimals and 12 digits before the point.", "bad_amount")
    return amount


def platform_wallet(role):
    try:
        return Wallet.from_seed(PlatformWallet.objects.get(role=role).seed)
    except PlatformWallet.DoesNotExist as exc:
        raise InvalidTransition("The platform wallets are missing. Run bootstrap first.", "not_bootstrapped") from exc


def record(lease, kind, summary, tx_hash=""):
    return LedgerEvent.objects.create(lease=lease, kind=kind, summary=summary[:300], tx_hash=tx_hash or "")


def live_cache_key(lease_id):
    return f"lease-live:{lease_id}"


def ledger_now():
    """The ledger's clock for DISPLAY (countdown, which buttons to show): one reading reused for 10 s.

    Transitions never rely on this; they ask the ledger again. If the ledger can't be
    reached the local clock is used so pages still render.
    """
    offset = cache.get("ledger-clock-offset")
    if offset is None:
        try:
            offset = (services.ledger_close_time() - timezone.now()).total_seconds()
        except LedgerError:
            return timezone.now()
        cache.set("ledger-clock-offset", offset, LIVE_TTL)
    return timezone.now() + timedelta(seconds=offset)


_locks = {}
_locks_guard = threading.Lock()


@contextmanager
def _exclusive(lease):
    """Allow one action per lease at a time; a second one is refused instead of queued."""
    with _locks_guard:
        lock = _locks.setdefault(str(lease.pk), threading.Lock())
    if not lock.acquire(blocking=False):
        raise InvalidTransition("Another action on this deposit is still being processed. Give it a few seconds.", "busy")
    try:
        yield
    finally:
        lock.release()


def _require_status(lease, status, doing):
    if lease.status != status:
        raise InvalidTransition(
            f"You can't {doing} while the deposit is '{lease.get_status_display().lower()}'.", "wrong_status"
        )


def _advance(lease, expected, new_status, **fields):
    """Move the status only if it is still `expected`. -> True when this call made the change."""
    changed = Lease.objects.filter(pk=lease.pk, status=expected).update(
        status=new_status, updated_at=timezone.now(), **fields
    )
    lease.refresh_from_db()
    cache.delete(live_cache_key(lease.pk))
    return bool(changed)


def _spawn(fn, *args):
    """Run fn in a daemon thread and give its database connection back when it ends."""

    def main():
        try:
            fn(*args)
        except Exception:  # fn handles its own errors; this is the last line of defence
            log.exception("background task %s failed", fn.__name__)
        finally:
            connection.close()

    threading.Thread(target=main, daemon=True, name=f"{fn.__name__}-{args[0] if args else ''}").start()


# ---- setup: DRAFT -> SETTLEMENT_READY ----

def start_setup(lease):
    """Create the lease's 2-of-3 settlement account in the background (about 20 s)."""
    claimed = Lease.objects.filter(pk=lease.pk, status=S.DRAFT, setup_step="").update(setup_step="starting")
    if not claimed:
        raise InvalidTransition("Setup has already been started for this deposit.", "wrong_status")
    lease.setup_step = "starting"
    _spawn(_run_setup, lease.pk)


_treasury_guard = threading.Lock()
TREASURY_KEEP_XRP = Decimal("3")  # reserve plus fees that must stay in the treasury


def _funded_treasury():
    """The treasury wallet, topped up from the test faucet first when it can't pay for a settlement account."""
    treasury = platform_wallet("treasury")
    with _treasury_guard:
        if services.get_balances(treasury.address)["XRP"] < services.SETTLEMENT_FUND_XRP + TREASURY_KEEP_XRP:
            log.info("treasury is low on XRP; asking the faucet")
            services.faucet_top_up(treasury)
    return treasury


def _run_setup(lease_id):
    lease = Lease.objects.select_related("tenant", "landlord", "arbiter", "currency").get(pk=lease_id)
    code = lease.currency.code

    def on_step(step, tx_hash):
        Lease.objects.filter(pk=lease_id).update(setup_step=step, updated_at=timezone.now())
        record(lease, f"setup_{step}", SETUP_SUMMARIES.get(step, step).format(code=code), tx_hash)

    try:
        address, seed = services.create_settlement_account(
            _funded_treasury(), [p.address for p in lease.signers()], code, lease.currency.issuer_address, on_step
        )
    except Exception as exc:
        if isinstance(exc, (LedgerError, InvalidTransition)):
            message = exc.message
        else:
            log.exception("settlement setup failed for lease %s", lease_id)
            message = "Setting up the settlement account failed unexpectedly."
        if _advance(lease, S.DRAFT, S.FAILED, error=message):
            record(lease, "failed", f"Setup failed: {message}")
        return
    _advance(lease, S.DRAFT, S.SETTLEMENT_READY, settlement_address=address, settlement_seed=seed, error="")


# ---- escrow: SETTLEMENT_READY -> ACTIVE ----

def _adopt_escrow_from_ledger(lease):
    """Pick up an escrow that exists on the ledger but that we never recorded. -> True when the lease became ACTIVE.

    Happens when the EscrowCreate went through and its confirmation was lost (network failure).
    The lease's condition is unique, so the escrow can be found by it and is then verified in full.
    """
    tx_hash = services.find_escrow_create_hash(lease.tenant.address, lease.settlement_address, lease.condition_hex)
    if not tx_hash:
        return False
    found = services.get_transaction(tx_hash)
    if verify_escrow_create_tx(lease, found):
        return False
    tx = found["tx"]
    _mark_active(lease, tx["Account"], int(tx.get("Sequence") or tx.get("TicketSequence")), tx_hash)
    log.info("lease %s: adopted an escrow that was already on the ledger", lease.pk)
    return lease.status == S.ACTIVE


def _mark_active(lease, owner, sequence, tx_hash):
    if _advance(lease, S.SETTLEMENT_READY, S.ACTIVE, escrow_owner=owner, escrow_sequence=sequence):
        record(lease, "escrow_created", f"{lease.tenant.label} locked {money(lease, lease.amount)} in escrow", tx_hash)


def verify_escrow_create_tx(lease, found):
    """Check a wallet-submitted transaction is exactly this lease's deposit escrow. -> list of problems.

    `found` is the result of services.get_transaction() (None when the network doesn't know the hash).
    """
    if found is None:
        return ["the network doesn't know this transaction yet (wait a few seconds and try again)"]
    if not found.get("validated"):
        return ["the transaction isn't in a validated ledger yet (wait a few seconds and try again)"]
    if found.get("result") != "tesSUCCESS":
        return [f"the transaction failed on the ledger ({found.get('result')})"]
    tx = found.get("tx") or {}
    problems = []
    if tx.get("TransactionType") != "EscrowCreate":
        return ["it is not an escrow creation"]
    if tx.get("Account") != lease.tenant.address:
        problems.append("it wasn't sent by the tenant")
    if tx.get("Destination") != lease.settlement_address:
        problems.append("it doesn't pay into this lease's settlement account")
    amount = tx.get("Amount")
    if not isinstance(amount, dict):
        problems.append("it locks XRP instead of the deposit token")
    else:
        if amount.get("currency") != lease.currency.code or amount.get("issuer") != lease.currency.issuer_address:
            problems.append("it uses a different token or issuer")
        try:
            if Decimal(str(amount.get("value"))) != lease.amount:
                problems.append("the amount is not the agreed deposit")
        except InvalidOperation:
            problems.append("the amount can't be read")
    if (tx.get("Condition") or "").upper() != lease.condition_hex.upper():
        problems.append("it carries a different release condition")
    if tx.get("CancelAfter") != datetime_to_ripple_time(lease.cancel_after):
        problems.append("its automatic return date is not the lease end plus the grace period")
    if tx.get("FinishAfter") is not None:
        problems.append("it has an earliest release date, which this deposit must not have")
    return problems


def register_wallet_escrow(lease, tx_hash):
    """Wallet mode: accept an EscrowCreate the tenant signed in their own wallet, after checking it on the ledger."""
    tx_hash = str(tx_hash or "").strip().upper()
    if not re.fullmatch(r"[0-9A-F]{64}", tx_hash):
        raise InvalidInput("That doesn't look like a transaction hash.", "bad_tx_hash")
    with _exclusive(lease):
        lease.refresh_from_db()
        _require_status(lease, S.SETTLEMENT_READY, "lock the deposit")
        found = services.get_transaction(tx_hash)
        problems = verify_escrow_create_tx(lease, found)
        if problems:
            raise InvalidInput("This transaction can't be accepted as the deposit: " + "; ".join(problems) + ".", "escrow_mismatch")
        tx = found["tx"]
        _mark_active(lease, tx["Account"], int(tx.get("Sequence") or tx.get("TicketSequence")), tx_hash)
    return lease


# ---- release: ACTIVE -> IN_SETTLEMENT ----

def release(lease, party, reason):
    """The tenant or landlord releases the escrow into the settlement account, before the automatic return date.

    The platform's bot account submits the EscrowFinish (the ledger lets any account do it), so the
    release secret never leaves the server. The deposit can only go to the 2-of-3 settlement account.
    """
    if reason not in Lease.ReleaseReason.values:
        raise InvalidInput("The reason must be early_move_out or damage_claim.", "bad_reason")
    with _exclusive(lease):
        lease.refresh_from_db()
        _require_status(lease, S.ACTIVE, "release the deposit")
        role = lease.role_of(party)
        if role not in ("tenant", "landlord"):
            raise InvalidTransition("Only the tenant or the landlord can release the deposit.", "not_allowed")
        try:
            tx_hash = services.finish_escrow(
                platform_wallet("bot"), lease.escrow_owner, lease.escrow_sequence, lease.condition_hex,
                lease.fulfillment_hex, lease.cancel_after,
            )
        except LedgerError as exc:
            if exc.engine_result == "escrow_expired":
                raise InvalidTransition(exc.message, "escrow_expired") from exc
            raise
        _after_release(lease, party, role, reason, tx_hash)
    return lease


def _after_release(lease, party, role, reason, tx_hash):
    if _advance(lease, S.ACTIVE, S.IN_SETTLEMENT, release_reason=reason):
        what = "moved out early" if reason == Lease.ReleaseReason.EARLY_MOVE_OUT else "claimed damage"
        record(
            lease, "released",
            f"{party.label} ({role}) {what}: deposit moved to the 2-of-3 settlement account", tx_hash,
        )
    if reason == Lease.ReleaseReason.EARLY_MOVE_OUT:
        try:
            _propose(lease, party, lease.amount, Decimal("0"), "Early move-out: the full deposit goes back to the tenant.")
        except (LedgerError, InvalidInput, InvalidTransition) as exc:
            # The deposit is safe in the settlement account; anyone can still propose by hand.
            log.warning("default proposal for lease %s failed: %s", lease.pk, exc)


# ---- settlement: propose and sign ----

def _payouts(lease, tenant_amount, landlord_amount):
    """Non-zero payouts in the order prepare_payout_txs keeps them."""
    pairs = [(lease.tenant.address, tenant_amount), (lease.landlord.address, landlord_amount)]
    return [(address, amount) for address, amount in pairs if amount > 0]


def propose(lease, party, tenant_amount, landlord_amount, reason=""):
    """Propose how to split the deposit. Replaces any open proposal; the proposer's approval is added at once."""
    with _exclusive(lease):
        lease.refresh_from_db()
        return _propose(lease, party, tenant_amount, landlord_amount, reason)


def _propose(lease, party, tenant_amount, landlord_amount, reason):
    _require_status(lease, S.IN_SETTLEMENT, "propose a split")
    if lease.role_of(party) is None:
        raise InvalidTransition("Only the tenant, landlord or arbiter of this lease can propose a split.", "not_allowed")
    tenant_amount = parse_amount(tenant_amount, "tenant_amount")
    landlord_amount = parse_amount(landlord_amount, "landlord_amount")
    reason = str(reason or "").strip()
    if len(reason) > 500:
        raise InvalidInput("Keep the reason under 500 characters.", "bad_reason")
    open_proposals = list(lease.proposals.filter(status=SettlementProposal.Status.OPEN))
    if any(p.executed_hashes for p in open_proposals):
        raise InvalidTransition("A payout is already under way. Approve the open split again to finish it.", "payout_in_progress")

    code, issuer = lease.currency.code, lease.currency.issuer_address
    balance = services.get_token_balance(lease.settlement_address, code, issuer)
    if tenant_amount + landlord_amount != balance:
        raise InvalidInput(
            f"The two amounts must add up to exactly {money(lease, balance)}, the amount held in the settlement account.",
            "payout_sum_mismatch",
        )
    prepared = services.prepare_payout_txs(lease.settlement_address, _payouts(lease, tenant_amount, landlord_amount), code, issuer)
    for old in open_proposals:
        old.status = SettlementProposal.Status.SUPERSEDED
        old.save(update_fields=["status"])
    proposal = SettlementProposal.objects.create(
        lease=lease, tenant_amount=tenant_amount, landlord_amount=landlord_amount, reason=reason,
        proposed_by=party, prepared_txs=prepared, signatures={},
    )
    record(
        lease, "proposed",
        f"{party.label} proposed a split: {money(lease, tenant_amount)} to the tenant, "
        f"{money(lease, landlord_amount)} to the landlord (needs {QUORUM} approvals)",
    )
    return proposal


def complete_signers(proposal):
    """Party ids (strings) that signed every prepared payout."""
    return [key for key, signed in proposal.signatures.items() if signed and len(signed) == len(proposal.prepared_txs) and all(signed)]


def next_unsigned_index(proposal, party):
    """The first prepared payout this party hasn't signed yet, or None."""
    mine = proposal.signatures.get(str(party.pk)) or []
    for index in range(len(proposal.prepared_txs)):
        if index >= len(mine) or not mine[index]:
            return index
    return None


def add_signature(proposal, party, index, blob_hex):
    """Store one party's Xaman multisignature for one prepared payout.

    The blob is checked (right signer, own key, exactly this transaction) before it is stored.
    When two parties have signed every payout, the payouts are submitted and the lease settles.
    """
    lease = proposal.lease
    with _exclusive(lease):
        lease.refresh_from_db()
        proposal.refresh_from_db()
        if proposal.status != SettlementProposal.Status.OPEN:
            raise InvalidTransition("This proposal is no longer open.", "proposal_closed")
        _require_status(lease, S.IN_SETTLEMENT, "approve a split")
        role = lease.role_of(party)
        if role is None:
            raise InvalidTransition("Only the tenant, landlord or arbiter of this lease can approve a split.", "not_allowed")
        if not isinstance(index, int) or not 0 <= index < len(proposal.prepared_txs):
            raise InvalidInput("That payout doesn't belong to this proposal.", "bad_index")
        if len(complete_signers(proposal)) >= QUORUM:
            raise InvalidTransition("This split already has its approvals.", "already_approved")
        signed = services.verify_multisig_blob(proposal.prepared_txs[index], blob_hex, party.address)
        key = str(party.pk)
        mine = list(proposal.signatures.get(key) or [None] * len(proposal.prepared_txs))
        mine[index] = signed
        proposal.signatures = {**proposal.signatures, key: mine}
        proposal.save(update_fields=["signatures"])
        if all(mine):
            record(lease, "signed", f"{party.label} ({role}) approved the split ({min(len(complete_signers(proposal)), QUORUM)} of {QUORUM} approvals)")
        if len(complete_signers(proposal)) >= QUORUM:
            _execute(proposal, lease)
    return proposal


def retry_payout(proposal, party):
    """Submit the remaining payouts again after a failure (both approvals are already in)."""
    lease = proposal.lease
    with _exclusive(lease):
        lease.refresh_from_db()
        proposal.refresh_from_db()
        if proposal.status != SettlementProposal.Status.OPEN or len(complete_signers(proposal)) < QUORUM:
            raise InvalidTransition("There is no approved payout to retry.", "nothing_to_retry")
        if lease.role_of(party) is None:
            raise InvalidTransition("Only the parties of this lease can retry the payout.", "not_allowed")
        _execute(proposal, lease)
    return proposal


def _execute(proposal, lease):
    """Submit the prepared payouts in order. Expired ones are prepared again and need new approvals."""
    try:
        _submit_remaining(proposal, lease)
    except ExpiredPreparedTx:
        _prepare_again(proposal, lease)
        return
    finally:
        cache.delete(live_cache_key(lease.pk))
    proposal.status = SettlementProposal.Status.EXECUTED
    proposal.save(update_fields=["status"])
    if _advance(lease, S.IN_SETTLEMENT, S.SETTLED):
        record(
            lease, "settled",
            f"Deposit settled: {money(lease, proposal.tenant_amount)} to the tenant, "
            f"{money(lease, proposal.landlord_amount)} to the landlord",
        )


def _submit_remaining(proposal, lease):
    names = {lease.tenant.address: f"{lease.tenant.label} (tenant)", lease.landlord.address: f"{lease.landlord.label} (landlord)"}
    signers = complete_signers(proposal)
    for index in range(len(proposal.executed_hashes), len(proposal.prepared_txs)):
        tx = proposal.prepared_txs[index]
        signed = [proposal.signatures[key][index] for key in signers]
        tx_hash = services.execute_multisigned(tx, signed)
        # Saved after every payout, so a retry never pays the same one twice.
        proposal.executed_hashes = [*proposal.executed_hashes, tx_hash]
        proposal.save(update_fields=["executed_hashes"])
        to = names.get(tx.get("Destination"), tx.get("Destination"))
        record(lease, "payout", f"{money(lease, tx['Amount']['value'])} paid to {to}", tx_hash)


def _prepare_again(proposal, lease):
    """The prepared payouts expired before both approvals arrived: rebuild the unpaid ones; approvals start over."""
    done = len(proposal.executed_hashes)
    remaining = _payouts(lease, proposal.tenant_amount, proposal.landlord_amount)[done:]
    fresh = services.prepare_payout_txs(lease.settlement_address, remaining, lease.currency.code, lease.currency.issuer_address)
    proposal.prepared_txs = proposal.prepared_txs[:done] + fresh
    proposal.signatures = {key: list(signed[:done]) + [None] * len(fresh) for key, signed in proposal.signatures.items()}
    proposal.save(update_fields=["prepared_txs", "signatures"])
    record(lease, "approvals_expired", "The approvals expired before the payout went through. Approve the split again.")


# ---- evidence: a photo's SHA-256 anchored in a public memo ----

EVIDENCE_MEMO_TYPE = "safedeposit/evidence"


def evidence_memo(item):
    """What goes on the ledger for a photo: ids and the hash only, never names or the picture."""
    return {"lease": str(item.lease_id), "kind": item.kind, "sha256": item.sha256}


def _mark_anchored(item, tx_hash):
    changed = Evidence.objects.filter(pk=item.pk, anchor_tx_hash="").update(anchor_tx_hash=tx_hash)
    item.refresh_from_db()
    if changed:
        record(
            item.lease, "evidence_anchored",
            f"{item.uploaded_by.label} anchored a {item.get_kind_display().lower()} photo's fingerprint on the ledger", tx_hash,
        )
    return item


def register_wallet_anchor(item, tx_hash):
    """Wallet mode: accept the anchoring transaction the uploader signed, after checking it on the ledger."""
    tx_hash = str(tx_hash or "").strip().upper()
    if not re.fullmatch(r"[0-9A-F]{64}", tx_hash):
        raise InvalidInput("That doesn't look like a transaction hash.", "bad_tx_hash")
    if item.anchor_tx_hash:
        raise InvalidTransition("This photo is already anchored.", "already_anchored")
    found = services.get_transaction(tx_hash)
    if found is None or not found.get("validated"):
        raise InvalidInput("The network hasn't validated this transaction yet. Wait a few seconds and try again.", "anchor_mismatch")
    tx = found.get("tx") or {}
    if (
        found.get("result") != "tesSUCCESS"
        or tx.get("Account") != item.uploaded_by.address
        or services.read_memo(tx, EVIDENCE_MEMO_TYPE) != evidence_memo(item)
    ):
        raise InvalidInput("That transaction doesn't anchor this photo from the uploader's wallet.", "anchor_mismatch")
    return _mark_anchored(item, tx_hash)


# ---- cancel, and the bot: ACTIVE -> RETURNED ----

CANCELLABLE = (S.PENDING_ACCEPTANCE, S.DRAFT, S.SETTLEMENT_READY)


def cancel(lease, message, kind="cancelled"):
    """Close an agreement before the deposit is locked. -> True when this call cancelled it."""
    for status in CANCELLABLE:
        if _advance(lease, status, S.CANCELLED, error=message):
            record(lease, kind, message)
            return True
    return False

def bot_pass():
    """Return every deposit whose automatic return date (+10 s) has passed on the ledger.

    -> list of {'lease_id', 'action', ...}.
    """
    related = ("tenant", "landlord", "arbiter", "currency")
    pending = Lease.objects.filter(status=S.PENDING_ACCEPTANCE).select_related(*related)
    active = Lease.objects.filter(status=S.ACTIVE, escrow_sequence__isnull=False).select_related(*related)
    unlocked = Lease.objects.filter(status=S.SETTLEMENT_READY).select_related(*related)
    if not pending.exists() and not active.exists() and not unlocked.exists():
        return []
    now = services.ledger_close_time()
    due = lambda lease: now > lease.cancel_after + services.CANCEL_SAFETY  # noqa: E731
    results = []
    # Both lists are read before anything changes, so a lease is handled once per pass.
    for step, leases in ((_expire_pending, list(pending)), (_expire_unlocked, list(unlocked)), (_return_or_reconcile, list(active))):
        for lease in [lease for lease in leases if due(lease)]:
            try:
                with _exclusive(lease):
                    results.append({"lease_id": str(lease.pk), **step(lease)})
            except (LedgerError, InvalidTransition) as exc:
                log.warning("bot: lease %s not processed: %s", lease.pk, exc)
                results.append({"lease_id": str(lease.pk), "action": "error", "error": exc.message})
            except Exception:
                log.exception("bot: lease %s failed", lease.pk)
                results.append({"lease_id": str(lease.pk), "action": "error", "error": "Unexpected error; see the server log."})
    return results


def _expire_pending(lease):
    """An agreement nobody finished accepting before its return date can never start."""
    message = "Not everyone accepted before the automatic return date, so this agreement can no longer start."
    return {"action": "cancelled_pending" if cancel(lease, message, "expired") else "skipped"}


def _expire_unlocked(lease):
    """A lease that passed its return date without a recorded escrow: recover a lost one, else close the lease."""
    lease.refresh_from_db()
    if lease.status != S.SETTLEMENT_READY:
        return {"action": "skipped"}
    if _adopt_escrow_from_ledger(lease):
        return _return_or_reconcile(lease)
    message = "The deposit wasn't locked before the automatic return date, so this lease can no longer start."
    if _advance(lease, S.SETTLEMENT_READY, S.FAILED, error=message):
        record(lease, "failed", message)
    return {"action": "expired_unlocked"}


def _return_or_reconcile(lease):
    lease.refresh_from_db()
    if lease.status != S.ACTIVE:
        return {"action": "skipped"}
    if services.get_escrow(lease.escrow_owner, lease.escrow_sequence) is None:
        return _reconcile(lease)
    try:
        tx_hash = services.cancel_escrow(platform_wallet("bot"), lease.escrow_owner, lease.escrow_sequence, lease.cancel_after)
    except LedgerError:
        # Someone else may have cancelled it in the same moment (a second bot, or anyone at all).
        if services.get_escrow(lease.escrow_owner, lease.escrow_sequence) is not None:
            raise
        return _reconcile(lease)
    if _advance(lease, S.ACTIVE, S.RETURNED):
        record(lease, "returned", f"Deposit returned to {lease.tenant.label} automatically", tx_hash)
    return {"action": "returned", "tx_hash": tx_hash}


def _reconcile(lease):
    """The escrow is gone without us: read from the ledger where the deposit went."""
    balance = services.get_token_balance(lease.settlement_address, lease.currency.code, lease.currency.issuer_address)
    if balance > 0:
        if _advance(lease, S.ACTIVE, S.IN_SETTLEMENT):
            record(lease, "released", "Deposit was released to the settlement account (detected on the ledger)")
        return {"action": "reconciled_in_settlement"}
    if _advance(lease, S.ACTIVE, S.RETURNED):
        record(lease, "returned", f"Deposit was returned to {lease.tenant.label} (detected on the ledger)")
    return {"action": "reconciled_returned"}


# ---- what the current persona may do ----

def open_proposal(lease):
    return lease.proposals.filter(status=SettlementProposal.Status.OPEN).first()


def allowed_actions(lease, party, now=None):
    """Action names for the UI. The API enforces the same list."""
    role = lease.role_of(party)
    if role is None:
        return []
    actions = []
    if lease.status == S.PENDING_ACCEPTANCE:
        if role in ("tenant", "landlord"):
            actions.append("change_arbiter")
            if lease.arbiter_id and not getattr(lease, f"{role}_approved_arbiter"):
                actions.append("approve_arbiter")
        if lease.created_by_id == party.pk:
            actions.append("cancel")
        return actions
    if lease.status == S.SETTLEMENT_READY and lease.created_by_id == party.pk:
        actions.append("cancel")
    if lease.status == S.SETTLEMENT_READY and role == "tenant":
        actions.insert(0, "lock_deposit")
    if lease.status == S.ACTIVE and (now or ledger_now()) < lease.cancel_after:
        if role == "tenant":
            actions.append("release_early")
        if role == "landlord":
            actions.append("claim_damage")
    if lease.status == S.IN_SETTLEMENT:
        proposal = open_proposal(lease)
        if proposal is None or not proposal.executed_hashes:
            actions.append("propose")
        if proposal is not None:
            if len(complete_signers(proposal)) >= QUORUM:
                actions.append(f"retry:{proposal.pk}")
            elif next_unsigned_index(proposal, party) is not None:
                actions.append(f"sign:{proposal.pk}")
    if role in ("tenant", "landlord") and lease.status in (S.SETTLEMENT_READY, S.ACTIVE, S.IN_SETTLEMENT):
        actions.append("upload_evidence")
    if lease.escrow_sequence is not None:
        actions.append("share_verify")
    return actions
