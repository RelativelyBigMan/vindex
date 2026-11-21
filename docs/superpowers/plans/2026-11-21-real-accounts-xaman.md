# Real Accounts with Xaman Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the demo personas with real Xaman-signed accounts on XRPL Testnet: sign-in, three-party agreements via invite links, Xaman signing for every user action (multisign for settlement), and an in-app test faucet.

**Architecture:** The server talks to Xaman's REST API (`ledger/xaman.py`) with the API key and secret; the browser only talks to our own JSON API and shows a QR code the server renders. Each user action creates a `XamanRequest`; polling it finishes the action (log in, register the validated transaction, store a multisign signature). The ledger rules (`core/lifecycle.py`, `ledger/services.py`) stay; server-held user keys disappear. Agreements (`core/agreements.py`) and the faucet (`core/faucet.py`) are new, focused modules.

**Tech Stack:** Python 3.13, Django 5.2, xrpl-py 5.2, requests, segno (new), vanilla JS ES modules, Xaman REST API v1.

**Spec:** `docs/superpowers/specs/2026-11-21-real-accounts-xaman-design.md`

## Global Constraints

- XRPL Testnet only (Devnet when `XRPL_NETWORK=devnet`); every Xaman payload sets `options.force_network` to `"TESTNET"` (or `"DEVNET"`).
- The server never holds user keys. Platform wallets (treasury, bot, issuer_eur, issuer_usd) keep their seeds in `PlatformWallet`.
- The release fulfillment never leaves the server; the bot account submits EscrowFinish.
- No CDNs, no new browser hosts: CSP in `core/middleware.py` stays as is. The QR is a `data:` SVG URI made server-side with segno.
- No `innerHTML`, no inline scripts or styles; untrusted text via `textContent` (`ui.el`).
- Never pass `null` to `replaceChildren` (use `ui.fill`).
- Every on-ledger action writes a `LedgerEvent`. Memos: ids and hashes only.
- Django 5.2.x, no new frameworks. New Python dependency: `segno` only.
- Tests never touch the network: Xaman and ledger calls are mocked.
- Run tests with `.venv\Scripts\python manage.py test` (Windows) from `C:\Users\Gleb\Projects\bari\decelsus`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

| File | Responsibility |
|---|---|
| `ledger/xaman.py` (new) | Xaman REST client: create / get / cancel payloads, normalise status, QR data URI |
| `ledger/services.py` | + `verify_multisig_blob`; − credential functions |
| `ledger/tests.py` (new) | Xaman client and multisig-blob tests |
| `scripts/xaman_multisign_spike.py` (new) | Manual Testnet proof that Xaman multisign blobs combine and submit |
| `core/models.py` | Party = account; Lease pending fields; new `Invite`, `XamanRequest`, `FaucetGrant` |
| `core/migrations/0001_initial.py` | Fresh squashed migration |
| `core/lifecycle.py` | No server user keys: bot releases, `add_signature`, `retry_payout`, pending-lease expiry, new `allowed_actions` |
| `core/agreements.py` (new) | Drafts, invites, acceptance, arbiter approval, cancel |
| `core/faucet.py` (new) | Activate account, grant test tokens, 24 h limits |
| `core/signing.py` (new) | Start Xaman requests per action; refresh and finish them |
| `core/api.py` | Accounts, agreements, Xaman, faucet endpoints; demo / GemWallet / credential endpoints removed |
| `core/views.py`, `core/urls.py` | Sign-in-required pages, invite page; demo, stage, arbiter pages removed |
| `core/context.py`, `config/settings.py`, `.env.example`, `requirements.txt` | Xaman config, no `DEMO_MODE` |
| `core/management/commands/bootstrap.py` (renamed) | Platform wallets and currencies only |
| `core/management/commands/preflight.py` | Escrow round trip with throw-away wallets |
| `core/tests.py` | Ported lifecycle tests + new agreement / signing / faucet / API tests |
| `static/js/xaman.js` (new) | Sign-in / signing dialog: QR, deep link, polling |
| `static/js/actions.js`, `session.js`, `app.js`, `parts.js` | Xaman flows, signed-in identity, no demo bar |
| `static/js/pages/{dashboard,create,invite,lease,verify}.js` | Pages |
| `templates/...` | Header, home, dashboard, create, invite, lease; demo templates removed |
| `README.md`, `CLAUDE.md`, `scripts/setup.py`, `scripts/fetch_vendor.py` | Docs and setup |

Deleted: `static/js/wallet.js`, `static/js/pages/{demo,stage,arbiter}.js`, `templates/escrow/{demo,stage,arbiter}.html`, `templates/partials/_demo_bar.html`, `core/management/commands/{demo_scenarios,demo_reset,bootstrap_demo}.py`, `scripts/wallet_walkthrough.py`, `scripts/api_walkthrough.py`, `static/vendor/gemwallet-api-*.js`, `core/migrations/000[1-3]_*.py` (replaced).

---

### Task 1: Xaman REST client and multisig blob verification

**Files:**
- Create: `ledger/xaman.py`, `ledger/tests.py`
- Modify: `ledger/services.py` (add `verify_multisig_blob` after `execute_multisigned`), `config/settings.py`, `.env.example`, `requirements.txt`

**Interfaces:**
- Produces:
  - `xaman.configured() -> bool`
  - `xaman.create_payload(txjson: dict, *, submit: bool, multisign: bool = False, signers: list[str] | None = None, instruction: str = "", user_token: str = "") -> dict` → `{"uuid": str, "deeplink": str, "pushed": bool}`
  - `xaman.get_payload(uuid: str) -> dict` → `{"status": "open"|"signed"|"rejected"|"expired", "account": str, "hex": str, "txid": str, "user_token": str}`
  - `xaman.cancel_payload(uuid: str) -> None`
  - `xaman.qr_data_uri(text: str) -> str` (`data:image/svg+xml,...`)
  - `services.verify_multisig_blob(tx_dict: dict, blob_hex: str, signer_address: str) -> dict` (signed XRPL JSON with exactly one `Signers` entry; raises `LedgerError("signature_mismatch", ...)`)
  - Errors: `LedgerError("xaman_error", friendly)` and `LedgerError("xaman_not_configured", friendly)`.

- [ ] **Step 1: Add the dependency and settings**

`requirements.txt` becomes:

```
django>=5.2,<6
xrpl-py>=5.2
python-dotenv>=1.0
requests>=2.31
segno>=1.6
```

In `config/settings.py` replace the two `DEMO_MODE` lines with:

```python
# Xaman (https://apps.xumm.dev): the server creates sign requests with these. Never sent to the browser.
XAMAN_API_KEY = os.environ.get("XAMAN_API_KEY", "").strip()
XAMAN_API_SECRET = os.environ.get("XAMAN_API_SECRET", "").strip()
```

`.env.example` becomes:

```
# Copy to .env (never commit .env).
XRPL_NETWORK=testnet          # testnet | devnet
DJANGO_SECRET_KEY=change-me-to-a-long-random-string
DJANGO_DEBUG=1
DJANGO_HTTPS=0                # 1 only when served over HTTPS (secure cookies, HSTS)
# From https://apps.xumm.dev (free developer account). Without them nobody can sign in.
XAMAN_API_KEY=
XAMAN_API_SECRET=
```

Run: `.venv\Scripts\python -m pip install -r requirements.txt`
Expected: `Successfully installed segno-...` (or "already satisfied").

Leave every other `DEMO_MODE` reference alone in this task; Task 3 removes them. Until then add `DEMO_MODE = False` directly under the Xaman settings so imports keep working:

```python
DEMO_MODE = False  # removed in Task 3; kept so existing modules import
```

- [ ] **Step 2: Write the failing tests**

Create `ledger/tests.py`:

```python
"""Xaman client and multisignature checks. No network: HTTP is mocked, signatures are real."""
from unittest import mock

from django.test import SimpleTestCase, override_settings
from xrpl.core import binarycodec
from xrpl.wallet import Wallet

from ledger import services, xaman
from ledger.errors import LedgerError


def response(status=200, data=None):
    fake = mock.Mock(status_code=status)
    fake.json.return_value = data if data is not None else {}
    return fake


@override_settings(XAMAN_API_KEY="key", XAMAN_API_SECRET="secret", XRPL_NETWORK="testnet")
class XamanClientTests(SimpleTestCase):
    def test_create_payload_sends_network_options_and_credentials(self):
        with mock.patch.object(xaman.requests, "post", return_value=response(200, {
            "uuid": "u-1", "next": {"always": "https://xumm.app/sign/u-1"}, "pushed": True,
        })) as post:
            result = xaman.create_payload({"TransactionType": "SignIn"}, submit=False, instruction="Sign in", user_token="tok")
        self.assertEqual(result, {"uuid": "u-1", "deeplink": "https://xumm.app/sign/u-1", "pushed": True})
        url, kwargs = post.call_args.args[0], post.call_args.kwargs
        self.assertEqual(url, "https://xumm.app/api/v1/platform/payload")
        self.assertEqual(kwargs["headers"]["X-API-Key"], "key")
        self.assertEqual(kwargs["headers"]["X-API-Secret"], "secret")
        body = kwargs["json"]
        self.assertEqual(body["options"], {"submit": False, "expire": 5, "force_network": "TESTNET"})
        self.assertEqual(body["custom_meta"], {"instruction": "Sign in"})
        self.assertEqual(body["user_token"], "tok")

    def test_multisign_payload_lists_the_allowed_signer(self):
        with mock.patch.object(xaman.requests, "post", return_value=response(200, {"uuid": "u", "next": {"always": "x"}})) as post:
            xaman.create_payload({"TransactionType": "Payment"}, submit=False, multisign=True, signers=["rA"])
        options = post.call_args.kwargs["json"]["options"]
        self.assertEqual((options["multisign"], options["signers"]), (True, ["rA"]))

    @override_settings(XRPL_NETWORK="devnet")
    def test_devnet_forces_the_devnet(self):
        with mock.patch.object(xaman.requests, "post", return_value=response(200, {"uuid": "u", "next": {"always": "x"}})) as post:
            xaman.create_payload({"TransactionType": "SignIn"}, submit=False)
        self.assertEqual(post.call_args.kwargs["json"]["options"]["force_network"], "DEVNET")

    def test_get_payload_normalises_every_state(self):
        cases = [
            ({"meta": {"resolved": False, "signed": False, "cancelled": False, "expired": False}}, "open"),
            ({"meta": {"resolved": True, "signed": True}, "response": {"account": "rA", "hex": "AB", "txid": "T"},
              "application": {"issued_user_token": "tok"}}, "signed"),
            ({"meta": {"resolved": True, "signed": False, "cancelled": False, "expired": False}}, "rejected"),
            ({"meta": {"resolved": False, "signed": False, "cancelled": True}}, "rejected"),
            ({"meta": {"resolved": False, "signed": False, "cancelled": False, "expired": True}}, "expired"),
        ]
        for data, status in cases:
            with self.subTest(status=status), mock.patch.object(xaman.requests, "get", return_value=response(200, data)):
                self.assertEqual(xaman.get_payload("u")["status"], status)
        with mock.patch.object(xaman.requests, "get", return_value=response(200, cases[1][0])):
            self.assertEqual(xaman.get_payload("u"), {"status": "signed", "account": "rA", "hex": "AB", "txid": "T", "user_token": "tok"})

    def test_http_errors_become_friendly_ledger_errors(self):
        with mock.patch.object(xaman.requests, "post", return_value=response(403, {"error": {"reference": "x"}})):
            with self.assertRaises(LedgerError) as caught:
                xaman.create_payload({"TransactionType": "SignIn"}, submit=False)
        self.assertEqual(caught.exception.engine_result, "xaman_error")
        with mock.patch.object(xaman.requests, "get", side_effect=xaman.requests.ConnectionError("down")):
            with self.assertRaises(LedgerError):
                xaman.get_payload("u")

    @override_settings(XAMAN_API_KEY="", XAMAN_API_SECRET="")
    def test_missing_keys_are_reported(self):
        self.assertFalse(xaman.configured())
        with self.assertRaises(LedgerError) as caught:
            xaman.create_payload({"TransactionType": "SignIn"}, submit=False)
        self.assertEqual(caught.exception.engine_result, "xaman_not_configured")

    def test_qr_is_an_svg_data_uri(self):
        self.assertTrue(xaman.qr_data_uri("https://xumm.app/sign/u").startswith("data:image/svg+xml"))


class MultisigBlobTests(SimpleTestCase):
    def setUp(self):
        self.signer = Wallet.create()
        self.other = Wallet.create()
        self.tx = {
            "TransactionType": "Payment", "Account": Wallet.create().address, "Destination": self.other.address,
            "Amount": {"currency": "EUR", "issuer": Wallet.create().address, "value": "150"},
            "Sequence": 10, "Fee": "36", "LastLedgerSequence": 5000, "SigningPubKey": "",
        }

    def blob(self, tx, wallet):
        return binarycodec.encode(services.sign_prepared(tx, wallet))

    def test_a_valid_signature_from_the_expected_signer_is_returned_as_json(self):
        signed = services.verify_multisig_blob(self.tx, self.blob(self.tx, self.signer), self.signer.address)
        self.assertEqual(signed["Signers"][0]["Signer"]["Account"], self.signer.address)

    def test_wrong_signer_other_transaction_or_garbage_is_refused(self):
        tampered = {**self.tx, "Amount": {**self.tx["Amount"], "value": "999"}}
        for blob, address in (
            (self.blob(self.tx, self.other), self.signer.address),
            (self.blob(tampered, self.signer), self.signer.address),
            ("ZZ", self.signer.address),
        ):
            with self.subTest(address=address), self.assertRaises(LedgerError) as caught:
                services.verify_multisig_blob(self.tx, blob, address)
            self.assertEqual(caught.exception.engine_result, "signature_mismatch")

    def test_a_forged_signature_is_refused(self):
        signed = services.sign_prepared(self.tx, self.signer)
        signed["Signers"][0]["Signer"]["TxnSignature"] = "30" + signed["Signers"][0]["Signer"]["TxnSignature"][2:-2] + "00"
        with self.assertRaises(LedgerError):
            services.verify_multisig_blob(self.tx, binarycodec.encode(signed), self.signer.address)
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `.venv\Scripts\python manage.py test ledger -v 2`
Expected: ERROR, `ImportError: cannot import name 'xaman' from 'ledger'`.

- [ ] **Step 4: Write `ledger/xaman.py`**

```python
"""Xaman (formerly XUMM) REST client. Server side only: the API secret never reaches the browser.

Docs: https://docs.xaman.dev. One payload = one sign request shown to the user as a QR code or
deep link. We poll the payload by uuid; there are no webhooks, so no public callback URL is needed.
"""
import logging
from urllib.parse import quote

import requests
import segno
from django.conf import settings

from .client import network_key
from .errors import LedgerError

log = logging.getLogger(__name__)

API = "https://xumm.app/api/v1/platform"
TIMEOUT = 15  # seconds
EXPIRE_MINUTES = 5
FORCE_NETWORK = {"testnet": "TESTNET", "devnet": "DEVNET"}


def configured():
    return bool(settings.XAMAN_API_KEY and settings.XAMAN_API_SECRET)


def _headers():
    if not configured():
        raise LedgerError("xaman_not_configured", "Xaman isn't configured on this server (XAMAN_API_KEY / XAMAN_API_SECRET).")
    return {
        "X-API-Key": settings.XAMAN_API_KEY, "X-API-Secret": settings.XAMAN_API_SECRET,
        "Content-Type": "application/json", "Accept": "application/json",
    }


def _call(method, path, **kwargs):
    headers = _headers()
    try:
        answer = getattr(requests, method)(f"{API}{path}", headers=headers, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        log.warning("xaman %s %s failed: %s", method.upper(), path, exc)
        raise LedgerError("xaman_error", "Xaman can't be reached right now. Try again in a moment.") from exc
    if answer.status_code >= 400:
        log.warning("xaman %s %s answered %s: %s", method.upper(), path, answer.status_code, str(getattr(answer, "text", ""))[:300])
        raise LedgerError("xaman_error", "Xaman refused the request. Check the API key and secret, then try again.")
    try:
        return answer.json()
    except ValueError as exc:
        raise LedgerError("xaman_error", "Xaman sent an answer we couldn't read.") from exc


def create_payload(txjson, *, submit, multisign=False, signers=None, instruction="", user_token=""):
    """Create a sign request. -> {'uuid', 'deeplink', 'pushed'}"""
    options = {"submit": bool(submit), "expire": EXPIRE_MINUTES, "force_network": FORCE_NETWORK.get(network_key(), "TESTNET")}
    if multisign:
        options["multisign"] = True
    if signers:
        options["signers"] = list(signers)
    body = {"txjson": txjson, "options": options}
    if instruction:
        body["custom_meta"] = {"instruction": instruction[:280]}
    if user_token:
        body["user_token"] = user_token
    data = _call("post", "/payload", json=body)
    return {"uuid": data["uuid"], "deeplink": (data.get("next") or {}).get("always", ""), "pushed": bool(data.get("pushed"))}


def get_payload(uuid):
    """-> {'status': open|signed|rejected|expired, 'account', 'hex', 'txid', 'user_token'}"""
    data = _call("get", f"/payload/{quote(str(uuid))}")
    meta = data.get("meta") or {}
    reply = data.get("response") or {}
    if meta.get("signed"):
        status = "signed"
    elif meta.get("cancelled") or meta.get("resolved"):
        status = "rejected"
    elif meta.get("expired"):
        status = "expired"
    else:
        status = "open"
    return {
        "status": status, "account": reply.get("account") or "", "hex": reply.get("hex") or "",
        "txid": reply.get("txid") or "", "user_token": (data.get("application") or {}).get("issued_user_token") or "",
    }


def cancel_payload(uuid):
    try:
        _call("delete", f"/payload/{quote(str(uuid))}")
    except LedgerError:
        pass  # already resolved or expired: nothing to cancel


def qr_data_uri(text):
    """The QR code as an SVG data URI (rendered here, so the browser never contacts Xaman)."""
    return segno.make(text, error="m").svg_data_uri(scale=6, border=2, dark="#141414", light="#ffffff")
```

- [ ] **Step 5: Add `verify_multisig_blob` to `ledger/services.py`**

Add to the imports at the top of `ledger/services.py` (next to the existing xrpl imports):

```python
from xrpl.core import binarycodec, keypairs
```

Add after `execute_multisigned`:

```python
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
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `.venv\Scripts\python manage.py test ledger -v 2`
Expected: all `XamanClientTests` and `MultisigBlobTests` PASS.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt .env.example config/settings.py ledger/xaman.py ledger/services.py ledger/tests.py
git commit -m "Add Xaman REST client and multisignature blob verification"
```

---

### Task 2: Manual Testnet spike — Xaman multisign blobs combine and submit

This task is a gate. It needs real Xaman keys in `.env` and a phone with Xaman holding two Testnet accounts (create two accounts in Xaman, switch the app to Testnet in Settings → Advanced → Node, and fund both from https://xrpl.org/resources/dev-tools/xrp-faucets). If it fails, stop and report: the settlement design depends on it.

**Files:**
- Create: `scripts/xaman_multisign_spike.py`

**Interfaces:**
- Consumes: `xaman.create_payload`, `xaman.get_payload`, `xaman.qr_data_uri`, `services.verify_multisig_blob`, `services.execute_multisigned`, `services.prepare_payout_txs`, `services.fund_xrp`, `services.faucet_top_up`, `services.submit`, `ledger.client.get_client`.

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python
"""Manual proof on Testnet: two Xaman multisign approvals pay out of a 2-of-3 account.

    .venv\\Scripts\\python scripts\\xaman_multisign_spike.py rFirstXamanAccount rSecondXamanAccount

Creates a throw-away account (funded from the faucet) with a SignerList of the two Xaman accounts plus
a random third key (quorum 2), disables its master key, then asks each Xaman account to multisign a
10 XRP payment back to the first account. Open each printed link on the phone (or scan the QR file).
"""
import os
import sys
import time
from pathlib import Path

import django

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from xrpl.models import AccountInfo, AccountSet, AccountSetAsfFlag, Payment, SignerEntry, SignerListSet  # noqa: E402
from xrpl.transaction import autofill  # noqa: E402
from xrpl.wallet import Wallet  # noqa: E402

from ledger import services, xaman  # noqa: E402
from ledger.client import get_client  # noqa: E402


def wait_for(uuid):
    while True:
        info = xaman.get_payload(uuid)
        if info["status"] != "open":
            return info
        time.sleep(2)


def main(first, second):
    client = get_client()
    account = Wallet.create()
    services.faucet_top_up(account)
    print("multisig account:", account.address)
    third = Wallet.create().address
    entries = [SignerEntry(account=a, signer_weight=1) for a in (first, second, third)]
    services.submit(SignerListSet(account=account.address, signer_quorum=2, signer_entries=entries), account)
    services.submit(AccountSet(account=account.address, set_flag=AccountSetAsfFlag.ASF_DISABLE_MASTER), account)
    sequence = services._request(AccountInfo(account=account.address, ledger_index="validated"), client)["account_data"]["Sequence"]
    tx = Payment(account=account.address, destination=first, amount="10000000", sequence=sequence,
                 last_ledger_sequence=services.latest_validated_ledger_index(client) + 1000, signing_pub_key="")
    tx_dict = autofill(tx, client, signers_count=2).to_xrpl()
    print("prepared:", tx_dict)
    signed = []
    for signer in (first, second):
        payload = xaman.create_payload(tx_dict, submit=False, multisign=True, signers=[signer], instruction=f"Spike: approve as {signer}")
        qr = ROOT / f"spike-{signer}.svg.txt"
        qr.write_text(xaman.qr_data_uri(payload["deeplink"]), encoding="utf-8")
        print(f"\nOpen on the phone as {signer}: {payload['deeplink']}\n(QR data URI saved to {qr})")
        info = wait_for(payload["uuid"])
        print("status:", info["status"], "hex length:", len(info["hex"]))
        if info["status"] != "signed":
            sys.exit("Not signed; stopping.")
        signed.append(services.verify_multisig_blob(tx_dict, info["hex"], signer))
        print("signature verified for", signer)
    print("\nsubmitting:", services.execute_multisigned(tx_dict, signed))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
```

- [ ] **Step 2: Run it by hand on Testnet**

Run: `.venv\Scripts\python scripts\xaman_multisign_spike.py rFIRST rSECOND` (the two Xaman Testnet addresses)
Expected: two "signature verified" lines and a final transaction hash; the payment shows on https://testnet.xrpl.org.

If `verify_multisig_blob` refuses with "signed a different transaction", print `binarycodec.decode(info["hex"])` next to `tx_dict`, find the field Xaman changed, and record it in the commit message; adjust `_without_signatures` comparison only for that field and re-run Task 1's tests. If Xaman submits instead of returning the blob, stop and report.

- [ ] **Step 3: Delete the QR text files and commit**

```bash
del spike-*.svg.txt
git add scripts/xaman_multisign_spike.py
git commit -m "Add manual Xaman multisign spike (verified on Testnet)"
```

---

### Task 3: Accounts data model, fresh migration, bootstrap

**Files:**
- Modify: `core/models.py`, `core/admin.py`, `core/context.py`, `config/settings.py`
- Delete: `core/migrations/0001_initial.py`, `0002_leases.py`, `0003_lease_title_scenario.py`, `core/management/commands/demo_scenarios.py`, `core/management/commands/demo_reset.py`
- Rename: `core/management/commands/bootstrap_demo.py` → `core/management/commands/bootstrap.py`
- Modify: `ledger/services.py` (remove credential functions), `core/tests.py` (fixtures)

**Interfaces:**
- Produces (models):
  - `Party(address unique, display_name blank, xaman_user_token blank, created_at)`; `Party.label` property → display name or `rAbc…wxyz`.
  - `Lease`: `tenant`, `landlord`, `arbiter` nullable; `created_by` FK Party; `arbiter_accepted`, `tenant_approved_arbiter`, `landlord_approved_arbiter` booleans; statuses `PENDING_ACCEPTANCE` (default) and `CANCELLED` added; `scenario` and `archived` removed.
  - `Invite(lease, role, token unique, accepted_by, accepted_at, declined_at, revoked, created_at)`; `Invite.Role` = tenant | landlord | arbiter.
  - `XamanRequest(id UUID pk, xaman_uuid, purpose, party, lease, proposal, evidence, payload_index, deeplink, qr, status, error, result JSON, created_at)`; `Purpose` = signin | escrow_create | payout_sign | evidence_anchor | trustset; `Status` = open | signed | done | rejected | expired | failed.
  - `FaucetGrant(party, kind, created_at)`.
  - `PlatformWallet.Role` without `CREDENTIAL_ISSUER`.

- [ ] **Step 1: Replace `core/models.py`**

```python
"""Models: platform wallets, accounts, test currencies, leases, invites, Xaman requests and the timeline.

Only PLATFORM wallets keep seeds here (Testnet/Devnet only; ledger/client.py refuses mainnet).
Users sign with their own Xaman wallet; the server never holds their keys.
"""
import secrets
import uuid
from datetime import timedelta

from django.db import models

from ledger.conditions import make_condition


class PlatformWallet(models.Model):
    class Role(models.TextChoices):
        TREASURY = "treasury", "Treasury"
        BOT = "bot", "Bot"
        ISSUER_EUR = "issuer_eur", "EUR issuer"
        ISSUER_USD = "issuer_usd", "USD issuer"

    role = models.CharField(max_length=32, choices=Role.choices, unique=True)
    address = models.CharField(max_length=64, unique=True)
    seed = models.CharField(max_length=64)

    def __str__(self):
        return f"{self.get_role_display()} ({self.address})"


class Party(models.Model):
    """An account: one XRPL address, signed in with Xaman. Its role comes from each lease."""

    address = models.CharField(max_length=64, unique=True)
    display_name = models.CharField(max_length=80, blank=True, help_text="Shown to the other parties. Never sent to the ledger.")
    xaman_user_token = models.CharField(max_length=80, blank=True, help_text="Lets later sign requests arrive as push notifications.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "parties"

    @property
    def label(self):
        return self.display_name or f"{self.address[:6]}…{self.address[-4:]}"

    def __str__(self):
        return self.label


class Currency(models.Model):
    code = models.CharField(max_length=3, unique=True)
    label = models.CharField(max_length=32)
    issuer_address = models.CharField(max_length=64)

    class Meta:
        verbose_name_plural = "currencies"

    def __str__(self):
        return self.label


class Lease(models.Model):
    class Status(models.TextChoices):
        PENDING_ACCEPTANCE = "PENDING_ACCEPTANCE", "Waiting for acceptance"
        DRAFT = "DRAFT", "Being set up"
        SETTLEMENT_READY = "SETTLEMENT_READY", "Ready to lock"
        ACTIVE = "ACTIVE", "Locked"
        RETURNED = "RETURNED", "Returned"
        IN_SETTLEMENT = "IN_SETTLEMENT", "In settlement"
        SETTLED = "SETTLED", "Settled"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    class ReleaseReason(models.TextChoices):
        EARLY_MOVE_OUT = "early_move_out", "Early move-out"
        DAMAGE_CLAIM = "damage_claim", "Damage claim"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="leases_as_tenant", null=True, blank=True)
    landlord = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="leases_as_landlord", null=True, blank=True)
    arbiter = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="leases_as_arbiter", null=True, blank=True)
    created_by = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="leases_created")
    arbiter_accepted = models.BooleanField(default=False)
    tenant_approved_arbiter = models.BooleanField(default=False)
    landlord_approved_arbiter = models.BooleanField(default=False)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=20, decimal_places=6)
    title = models.CharField(max_length=120, blank=True, help_text="A label such as the property name. Never sent to the ledger.")
    end_at = models.DateTimeField()
    grace_minutes = models.PositiveIntegerField(default=1)
    cancel_after = models.DateTimeField(help_text="end_at + grace: from then on the deposit returns to the tenant.")

    settlement_address = models.CharField(max_length=64, blank=True)
    settlement_seed = models.CharField(max_length=64, blank=True, help_text="Testnet only; the master key is disabled.")
    condition_hex = models.CharField(max_length=128, blank=True)
    fulfillment_hex = models.CharField(max_length=128, blank=True, help_text="SECRET: releases the escrow.")
    escrow_owner = models.CharField(max_length=64, blank=True)
    escrow_sequence = models.BigIntegerField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_ACCEPTANCE, db_index=True)
    setup_step = models.CharField(max_length=20, blank=True)
    release_reason = models.CharField(max_length=20, choices=ReleaseReason.choices, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Lease {str(self.id)[:8]} ({self.status})"

    def save(self, *args, **kwargs):
        # Ledger time has whole seconds only, so the deadline must too.
        self.end_at = self.end_at.replace(microsecond=0)
        self.cancel_after = self.end_at + timedelta(minutes=self.grace_minutes)
        if not self.condition_hex:
            self.condition_hex, self.fulfillment_hex = make_condition()
        super().save(*args, **kwargs)

    def signers(self):
        """The three parties whose signatures count, in a fixed order (all set once setup starts)."""
        return [self.tenant, self.landlord, self.arbiter]

    def role_of(self, party):
        """'tenant' | 'landlord' | 'arbiter' for a party of this lease, else None."""
        if party is None:
            return None
        for role in ("tenant", "landlord", "arbiter"):
            if getattr(self, f"{role}_id") == party.pk:
                return role
        return None


def new_invite_token():
    return secrets.token_urlsafe(32)


class Invite(models.Model):
    class Role(models.TextChoices):
        TENANT = "tenant", "Tenant"
        LANDLORD = "landlord", "Landlord"
        ARBITER = "arbiter", "Arbiter"

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name="invites")
    role = models.CharField(max_length=10, choices=Role.choices)
    token = models.CharField(max_length=64, unique=True, default=new_invite_token)
    accepted_by = models.ForeignKey(Party, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    accepted_at = models.DateTimeField(null=True, blank=True)
    declined_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class SettlementProposal(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        EXECUTED = "EXECUTED", "Executed"
        SUPERSEDED = "SUPERSEDED", "Superseded"
        EXPIRED = "EXPIRED", "Expired"

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name="proposals")
    tenant_amount = models.DecimalField(max_digits=20, decimal_places=6)
    landlord_amount = models.DecimalField(max_digits=20, decimal_places=6)
    reason = models.CharField(max_length=500, blank=True)
    proposed_by = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="proposals")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    prepared_txs = models.JSONField(default=list, help_text="Unsigned payout transactions, in submit order.")
    signatures = models.JSONField(default=dict, help_text="{party_id: [signed tx JSON or null, one per prepared tx]}")
    executed_hashes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Proposal {self.pk} ({self.status})"


class Evidence(models.Model):
    class Kind(models.TextChoices):
        MOVE_IN = "move_in", "Move-in"
        MOVE_OUT = "move_out", "Move-out"
        DAMAGE = "damage", "Damage"

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name="evidence")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    file = models.FileField(upload_to="evidence/%Y%m%d/")
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(Party, on_delete=models.PROTECT, related_name="evidence")
    anchor_tx_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "evidence"


class LedgerEvent(models.Model):
    """One line of a lease's timeline. Every on-ledger action writes one, with its transaction hash."""

    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=32)
    tx_hash = models.CharField(max_length=64, blank=True)
    summary = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.summary


class XamanRequest(models.Model):
    """One Xaman sign request. The browser only sees our id, the QR code and the deep link."""

    class Purpose(models.TextChoices):
        SIGNIN = "signin", "Sign in"
        ESCROW_CREATE = "escrow_create", "Lock the deposit"
        PAYOUT_SIGN = "payout_sign", "Approve a payout"
        EVIDENCE_ANCHOR = "evidence_anchor", "Anchor a photo"
        TRUSTSET = "trustset", "Add a trust line"

    class Status(models.TextChoices):
        OPEN = "open", "Waiting for Xaman"
        SIGNED = "signed", "Signed, finishing"
        DONE = "done", "Done"
        REJECTED = "rejected", "Rejected in Xaman"
        EXPIRED = "expired", "Expired"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    xaman_uuid = models.CharField(max_length=64, unique=True)
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    party = models.ForeignKey(Party, on_delete=models.CASCADE, null=True, blank=True, related_name="xaman_requests")
    lease = models.ForeignKey(Lease, on_delete=models.CASCADE, null=True, blank=True)
    proposal = models.ForeignKey(SettlementProposal, on_delete=models.CASCADE, null=True, blank=True)
    evidence = models.ForeignKey(Evidence, on_delete=models.CASCADE, null=True, blank=True)
    payload_index = models.PositiveIntegerField(null=True, blank=True, help_text="Which prepared payout (payout_sign).")
    currency_code = models.CharField(max_length=3, blank=True, help_text="Trust line currency (trustset).")
    deeplink = models.URLField(max_length=300, blank=True)
    pushed = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    error = models.CharField(max_length=300, blank=True)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class FaucetGrant(models.Model):
    """One use of the Testnet faucet, for the once-per-24-hours limit."""

    party = models.ForeignKey(Party, on_delete=models.CASCADE, related_name="faucet_grants")
    kind = models.CharField(max_length=16, help_text="activate | tokens:EUR | tokens:USD")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
```

- [ ] **Step 2: Replace the migrations**

```bash
git rm core/migrations/0001_initial.py core/migrations/0002_leases.py core/migrations/0003_lease_title_scenario.py
.venv\Scripts\python manage.py makemigrations core
```
Expected: `core\migrations\0001_initial.py` created with every model above.

Delete the local database (it only holds demo data) and migrate:

```bash
del db.sqlite3
.venv\Scripts\python manage.py migrate
```
Expected: `Applying core.0001_initial... OK`.

- [ ] **Step 3: Update `core/admin.py`**

```python
from django.contrib import admin

from .models import Currency, Evidence, FaucetGrant, Invite, LedgerEvent, Lease, Party, PlatformWallet, SettlementProposal, XamanRequest


@admin.register(PlatformWallet)
class PlatformWalletAdmin(admin.ModelAdmin):
    list_display = ("role", "address")
    exclude = ("seed",)  # never show keys in the admin, even Testnet ones
    readonly_fields = ("role", "address")


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ("display_name", "address", "created_at")
    search_fields = ("display_name", "address")
    exclude = ("xaman_user_token",)


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "issuer_address")


@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "setup_step", "amount", "currency", "tenant", "landlord", "arbiter", "cancel_after", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("id", "settlement_address", "escrow_owner")
    # Secrets stay out of the admin; ledger facts are read-only so they can't drift from the ledger.
    exclude = ("settlement_seed", "fulfillment_hex")
    readonly_fields = (
        "end_at", "grace_minutes", "cancel_after", "settlement_address", "condition_hex",
        "escrow_owner", "escrow_sequence", "created_at", "updated_at",
    )


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    list_display = ("lease", "role", "accepted_by", "accepted_at", "declined_at", "revoked")
    exclude = ("token",)


@admin.register(SettlementProposal)
class SettlementProposalAdmin(admin.ModelAdmin):
    list_display = ("id", "lease", "status", "tenant_amount", "landlord_amount", "proposed_by", "created_at")
    list_filter = ("status",)
    readonly_fields = ("prepared_txs", "signatures", "executed_hashes")


@admin.register(Evidence)
class EvidenceAdmin(admin.ModelAdmin):
    list_display = ("id", "lease", "kind", "uploaded_by", "sha256", "anchor_tx_hash", "created_at")
    list_filter = ("kind",)


@admin.register(LedgerEvent)
class LedgerEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "lease", "kind", "summary", "tx_hash")
    list_filter = ("kind",)
    search_fields = ("summary", "tx_hash")


@admin.register(XamanRequest)
class XamanRequestAdmin(admin.ModelAdmin):
    list_display = ("created_at", "purpose", "status", "party", "lease")
    list_filter = ("purpose", "status")
    readonly_fields = ("result",)


@admin.register(FaucetGrant)
class FaucetGrantAdmin(admin.ModelAdmin):
    list_display = ("created_at", "party", "kind")
```

- [ ] **Step 4: Remove `DEMO_MODE` and add Xaman to the page config**

In `config/settings.py` delete the line `DEMO_MODE = False  # removed in Task 3; kept so existing modules import`.

Replace `core/context.py`:

```python
"""Template context shared by every page. Public facts only: this is embedded in the HTML."""
from ledger import xaman
from ledger.client import network, network_key

from .models import Currency


def escrow_config(request):
    net = network()
    return {
        "escrow_config": {
            "network": {"id": network_key(), "name": net["name"], "is_testnet": True, "explorer": net["explorer"], "ws": net["ws"]},
            "xaman_configured": xaman.configured(),
            "signed_in": bool(request.session.get("party_id")),
            "currencies": [
                {"code": c.code, "label": c.label, "issuer": c.issuer_address} for c in Currency.objects.order_by("code")
            ],
        }
    }
```

- [ ] **Step 5: Remove credential helpers from `ledger/services.py`**

Delete `LSF_CREDENTIAL_ACCEPTED`, `_hex`, `_text` (only if no other function uses them: run `grep -n "_hex(\|_text(" ledger/services.py` first and keep any still used), `get_credentials`, `issue_credential`, `accept_credential`.

- [ ] **Step 6: Rename and trim the bootstrap command**

```bash
git mv core/management/commands/bootstrap_demo.py core/management/commands/bootstrap.py
git rm core/management/commands/demo_scenarios.py core/management/commands/demo_reset.py
```

In `core/management/commands/bootstrap.py`:
- Module docstring: `"""Create (or reuse) the platform wallets (treasury, bot, token issuers) and the test tokens. Idempotent."""`
- `from core.models import Currency, PlatformWallet` (drop `Party`).
- `PLATFORM_ROLES = ["bot", "issuer_eur", "issuer_usd"]`; delete `PERSONAS`, `TOKEN_GRANTS`, `ARBITER_CREDENTIAL`, `ARBITER_CREDENTIAL_URI`.
- `help = "Create the platform wallets and test tokens on the XRPL test network (safe to rerun)."`
- In `handle`: delete the `parties = ...` line, the whole inner `for role, wallet in parties.items()` / `for role, target in TOKEN_GRANTS.items()` loops (keep the `Currency.objects.update_or_create` loop), and `self.arbiter_credential(...)`; call `self.summary(treasury, wallets)`.
- Delete the methods `arbiter_credential` and `persona`.
- In `faucet`, change "rerun bootstrap_demo" to "rerun bootstrap".
- `summary(self, treasury, wallets)`: drop the `names` / `parties` rows; name column prints `-`.

- [ ] **Step 7: Update the test fixtures in `core/tests.py`**

Replace the `MOCKED` list and `setUpTestData` / `make_lease`:

```python
MOCKED = [
    "ledger_close_time", "create_settlement_account", "create_escrow", "get_escrow", "finish_escrow", "cancel_escrow",
    "get_token_balance", "prepare_payout_txs", "sign_prepared", "execute_multisigned", "get_signer_list",
    "get_account_flags", "get_balances", "get_transaction", "build_escrow_create", "build_escrow_finish",
    "fund_xrp", "issue_tokens", "ensure_trust_line", "has_trust_line", "faucet_top_up",
    "find_escrow_create_hash", "anchor_memo", "build_anchor_memo", "get_account_raw", "account_exists",
    "verify_multisig_blob",
]
```

```python
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
```

Change `ISSUER = "rIssuerEUR"` to a lazily read value: delete the constant and, in every test that used `ISSUER`, use `self.eur.issuer_address`.

In `setUp`, delete `self.ledger.get_credentials.return_value = []` and add:

```python
        self.ledger.verify_multisig_blob.side_effect = lambda tx, blob, address: {**tx, "signed_by": address, "blob": blob}
```

`make_lease` becomes:

```python
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
```

Delete these test classes / tests now (they test removed demo features; Tasks 4–8 add replacements): `SetupAndEscrowTests.test_escrow_needs_a_ready_settlement_account`, `test_escrow_locks_the_deposit_and_stores_the_sequence`, `test_escrow_refused_when_the_tenant_is_short`, `WalletEscrowVerificationTests.test_escrow_already_on_the_ledger_is_adopted_instead_of_locked_twice`, the whole `ReleaseTests`, `ProposalTests`, `ActionsTests`, `ApiTests` and `DemoResetTests` classes.

In `BotTests.test_expired_escrow_is_cancelled_from_the_bot_wallet` the summary stays "Deposit returned to Giulia Rossi automatically" (Task 4 switches the lifecycle to `party.label`, which equals the display name here).

- [ ] **Step 8: Run what is left of the suite**

Run: `.venv\Scripts\python manage.py test core.tests.LeaseModelTests ledger`
Expected: `ledger` tests PASS; `LeaseModelTests` may ERROR because `core/lifecycle.py` still references `party.seed` / `wallet_for`. That is expected: do not commit yet. Tasks 3 and 4 are committed together at the end of Task 4.

---

### Task 4: Lifecycle without server-held user keys

**Files:**
- Modify: `core/lifecycle.py`, `core/tests.py`

**Interfaces:**
- Consumes: Task 3 models; `services.verify_multisig_blob`.
- Produces:
  - `lifecycle.release(lease, party, reason) -> Lease` — EscrowFinish submitted by the **bot** wallet.
  - `lifecycle.propose(lease, party, tenant_amount, landlord_amount, reason="") -> SettlementProposal` — no signatures added.
  - `lifecycle.add_signature(proposal, party, index: int, blob_hex: str) -> SettlementProposal`
  - `lifecycle.retry_payout(proposal, party) -> SettlementProposal`
  - `lifecycle.complete_signers(proposal) -> list[str]` (party ids, as strings, with every payout signed)
  - `lifecycle.next_unsigned_index(proposal, party) -> int | None`
  - `lifecycle.cancel(lease, message, kind="cancelled") -> bool` (moves PENDING_ACCEPTANCE / DRAFT / SETTLEMENT_READY → CANCELLED)
  - `lifecycle.allowed_actions(lease, party, now=None) -> list[str]` vocabulary: `change_arbiter`, `approve_arbiter`, `cancel`, `lock_deposit`, `release_early`, `claim_damage`, `propose`, `sign:<id>`, `retry:<id>`, `upload_evidence`, `share_verify`.
  - `lifecycle.platform_wallet(role)` raises `InvalidTransition(..., "not_bootstrapped")` with "Run bootstrap first."
  - Removed: `wallet_for`, `create_escrow_demo`, `register_wallet_release`, `verify_escrow_finish_tx`, `sign`, `anchor_evidence_demo`.

- [ ] **Step 1: Write the failing tests**

Append to `core/tests.py`:

```python
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
    def test_pending_agreement_past_its_return_date_is_cancelled_by_the_bot(self):
        lease = self.make_lease(S.PENDING_ACCEPTANCE)
        self.ledger.ledger_close_time.side_effect = lambda *a, **k: lease.cancel_after + timedelta(minutes=1)
        self.assertEqual(lifecycle.bot_pass(), [{"lease_id": str(lease.pk), "action": "cancelled_pending"}])
        lease.refresh_from_db()
        self.assertEqual(lease.status, S.CANCELLED)

    def test_cancel_only_before_the_deposit_is_locked(self):
        self.assertTrue(lifecycle.cancel(self.make_lease(S.SETTLEMENT_READY), "Stopped"))
        self.assertFalse(lifecycle.cancel(self.make_lease(S.ACTIVE), "Stopped"))
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv\Scripts\python manage.py test core.tests.ReleaseTests core.tests.ProposalTests core.tests.ActionsTests core.tests.CancelAndExpiryTests`
Expected: FAIL / ERROR (`add_signature` missing, release uses `wallet_for`, …). If the suite can't import `core.api`, run `.venv\Scripts\python manage.py test core.tests.ProposalTests` after Step 3 — the API is rewritten in Task 8; until then `core/urls.py` is not imported by these tests.

- [ ] **Step 3: Edit `core/lifecycle.py`**

3a. Module docstring first lines:

```python
"""Lease lifecycle: every status transition, each one guarded.

    PENDING_ACCEPTANCE -> DRAFT -> SETTLEMENT_READY -> ACTIVE -> RETURNED
                                                       ACTIVE -> IN_SETTLEMENT -> SETTLED
    FAILED on setup errors; CANCELLED before the deposit is locked.
```

3b. Delete `wallet_for`. Change `platform_wallet`'s message to `"The platform wallets are missing. Run bootstrap first."`.

3c. Every `party.display_name` / `lease.tenant.display_name` / `item.uploaded_by.display_name` in summaries and messages becomes `.label`.

3d. Delete `create_escrow_demo`. Keep `_adopt_escrow_from_ledger`, `_mark_active`, `verify_escrow_create_tx`, `register_wallet_escrow`.

3e. Replace `release` and delete `verify_escrow_finish_tx` and `register_wallet_release`:

```python
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
```

3f. In `_propose`, replace the `signatures = ...` line and the `create(...)` call:

```python
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
```

3g. Replace `sign` with:

```python
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
```

3h. Replace `_execute`, `_submit_remaining`, `_prepare_again`:

```python
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
```

Note: with `done > 0` the paid indexes stay signed, so `complete_signers` is only empty until each party re-signs the fresh payouts (indexes `done…`).

3i. Delete `anchor_evidence_demo`. Keep `evidence_memo`, `_mark_anchored`, `register_wallet_anchor`.

3j. Add `cancel` above the bot section:

```python
CANCELLABLE = (S.PENDING_ACCEPTANCE, S.DRAFT, S.SETTLEMENT_READY)


def cancel(lease, message, kind="cancelled"):
    """Close an agreement before the deposit is locked. -> True when this call cancelled it."""
    for status in CANCELLABLE:
        if _advance(lease, status, S.CANCELLED, error=message):
            record(lease, kind, message)
            return True
    return False
```

3k. In `bot_pass`, add pending agreements: replace the queryset section and loop tuple:

```python
    related = ("tenant", "landlord", "arbiter", "currency")
    pending = Lease.objects.filter(status=S.PENDING_ACCEPTANCE).select_related(*related)
    active = Lease.objects.filter(status=S.ACTIVE, escrow_sequence__isnull=False).select_related(*related)
    unlocked = Lease.objects.filter(status=S.SETTLEMENT_READY).select_related(*related)
    if not pending.exists() and not active.exists() and not unlocked.exists():
        return []
```

and

```python
    for step, leases in ((_expire_pending, list(pending)), (_expire_unlocked, list(unlocked)), (_return_or_reconcile, list(active))):
```

and add:

```python
def _expire_pending(lease):
    """An agreement nobody finished accepting before its return date can never start."""
    message = "Not everyone accepted before the automatic return date, so this agreement can no longer start."
    return {"action": "cancelled_pending" if cancel(lease, message, "expired") else "skipped"}
```

Also update the docstring line about archived leases: remove "Archived leases are included: …".

3l. Replace `allowed_actions`:

```python
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
```

(The fixture's `created_by` is the tenant, which is why the tenant gets `cancel` on a `SETTLEMENT_READY` lease in `test_actions_per_role_and_status`.)

3m. Remove now-unused imports (`Wallet` if unused, `Party` if unused). Run `.venv\Scripts\python -m pyflakes core/lifecycle.py` if available; otherwise `grep -n "Wallet\|Party" core/lifecycle.py`.

- [ ] **Step 4: Run the lifecycle tests**

Run: `.venv\Scripts\python manage.py test core.tests.LeaseModelTests core.tests.SetupAndEscrowTests core.tests.WalletEscrowVerificationTests core.tests.ReleaseTests core.tests.ProposalTests core.tests.BotTests core.tests.ActionsTests core.tests.CancelAndExpiryTests ledger`
Expected: PASS. (`core/api.py` still references removed names; it is not imported by these tests. If the runner imports it via URL checks, temporarily add nothing — fix in Task 8.)

- [ ] **Step 5: Commit Tasks 3 and 4 together**

```bash
git add -A core ledger config
git commit -m "Accounts data model and lifecycle without server-held user keys"
```

---

### Task 5: Agreements — drafts, invites, acceptance, arbiter approval

**Files:**
- Create: `core/agreements.py`
- Modify: `core/tests.py`

**Interfaces:**
- Consumes: `lifecycle.start_setup`, `lifecycle.cancel`, `lifecycle.record`, `lifecycle._advance`, `lifecycle.parse_amount`, models.
- Produces:
  - `agreements.MIN_SECONDS_TO_RETURN = 600`
  - `agreements.create_draft(creator, role, *, currency, amount, end_at, grace_minutes, title="", arbiter_address="") -> Lease`
  - `agreements.invite_for(token) -> Invite` (raises `InvalidInput("...", "invite_unknown")`)
  - `agreements.accept_invite(token, party) -> Lease`
  - `agreements.decline_invite(token, party) -> Lease`
  - `agreements.change_arbiter(lease, party, address) -> Lease`
  - `agreements.approve_arbiter(lease, party) -> Lease`
  - `agreements.cancel_by(lease, party) -> Lease`
  - `agreements.open_invites(lease) -> list[Invite]` (not revoked, not accepted, not declined)

- [ ] **Step 1: Write the failing tests**

Append to `core/tests.py` (add `Invite` to the models import):

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv\Scripts\python manage.py test core.tests.AgreementTests`
Expected: ERROR `ModuleNotFoundError: No module named 'core.agreements'`.

- [ ] **Step 3: Write `core/agreements.py`**

```python
"""Deposit agreements before anything goes on the ledger.

Tenant or landlord starts a draft; the other side and the arbiter join through single-use invite
links. Tenant and landlord must both approve the arbiter, and the arbiter must accept the role.
When all of that is true the lease moves to DRAFT and the settlement account is created.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from xrpl.core.addresscodec import is_valid_classic_address

from . import lifecycle
from .lifecycle import InvalidInput, InvalidTransition
from .models import Invite, Lease, Party

S = Lease.Status
MIN_SECONDS_TO_RETURN = 600  # setup takes about 20 s; the tenant needs time to lock afterwards
SIDES = ("tenant", "landlord")


def _other(role):
    return "landlord" if role == "tenant" else "tenant"


def create_draft(creator, role, *, currency, amount, end_at, grace_minutes, title="", arbiter_address=""):
    if role not in SIDES:
        raise InvalidInput("Choose whether you are the tenant or the landlord.", "bad_role")
    if end_at + timedelta(minutes=grace_minutes) < timezone.now() + timedelta(seconds=MIN_SECONDS_TO_RETURN):
        raise InvalidInput("The lease end plus the grace period must be at least 10 minutes from now.", "bad_end")
    with transaction.atomic():
        lease = Lease(created_by=creator, currency=currency, amount=amount, end_at=end_at,
                      grace_minutes=grace_minutes, title=title, status=S.PENDING_ACCEPTANCE)
        setattr(lease, role, creator)
        lease.save()
        Invite.objects.create(lease=lease, role=_other(role))
        Invite.objects.create(lease=lease, role=Invite.Role.ARBITER)
        lifecycle.record(lease, "created", f"{creator.label} started the agreement as {role}")
        if arbiter_address:
            _name_arbiter(lease, creator, role, arbiter_address)
    return lease


def invite_for(token):
    invite = Invite.objects.select_related("lease", "lease__currency").filter(token=str(token or "")).first()
    if invite is None or invite.revoked:
        raise InvalidInput("This invite link isn't valid any more. Ask for a new one.", "invite_unknown")
    return invite


def open_invites(lease):
    return [i for i in lease.invites.all() if not (i.revoked or i.accepted_by_id or i.declined_at)]


def _require_pending(lease, doing):
    if lease.status != S.PENDING_ACCEPTANCE:
        raise InvalidTransition(f"You can't {doing} any more: this agreement is '{lease.get_status_display().lower()}'.", "wrong_status")


def accept_invite(token, party):
    invite = invite_for(token)
    if invite.accepted_by_id == party.pk:
        return invite.lease
    if invite.accepted_by_id or invite.declined_at:
        raise InvalidTransition("This invite has already been used.", "invite_used")
    with transaction.atomic():
        lease = Lease.objects.select_for_update().get(pk=invite.lease_id)
        _require_pending(lease, "accept")
        current = lease.role_of(party)
        if invite.role in SIDES:
            if current is not None:
                raise InvalidTransition("You already have a role in this agreement.", "already_party")
            if getattr(lease, f"{invite.role}_id"):
                raise InvalidTransition("Someone has already taken that role.", "role_taken")
            setattr(lease, invite.role, party)
            if lease.arbiter_id:
                setattr(lease, f"{invite.role}_approved_arbiter", True)
            what = f"accepted the agreement as {invite.role}"
        else:
            if lease.arbiter_id and lease.arbiter_id != party.pk:
                raise InvalidTransition("This agreement names a different arbiter.", "wrong_arbiter")
            if current not in (None, "arbiter"):
                raise InvalidTransition("The tenant or landlord can't also be the arbiter.", "already_party")
            lease.arbiter, lease.arbiter_accepted = party, True
            what = "accepted the arbiter role"
        lease.save()
        invite.accepted_by, invite.accepted_at = party, timezone.now()
        invite.save(update_fields=["accepted_by", "accepted_at"])
        lifecycle.record(lease, "accepted", f"{party.label} {what}")
    _maybe_start(lease)
    return lease


def decline_invite(token, party):
    invite = invite_for(token)
    if invite.accepted_by_id:
        raise InvalidTransition("This invite has already been accepted.", "invite_used")
    lease = invite.lease
    _require_pending(lease, "decline")
    invite.declined_at = timezone.now()
    invite.save(update_fields=["declined_at"])
    lifecycle.cancel(lease, f"{party.label} declined the {invite.role} invite.", "declined")
    lease.refresh_from_db()
    return lease


def _name_arbiter(lease, actor, actor_role, address):
    address = str(address or "").strip()
    if not is_valid_classic_address(address):
        raise InvalidInput("The arbiter's address is not a valid XRP Ledger address.", "bad_address")
    arbiter, _ = Party.objects.get_or_create(address=address)
    if arbiter.pk in (lease.tenant_id, lease.landlord_id):
        raise InvalidInput("The arbiter must be someone other than the tenant and the landlord.", "bad_signers")
    lease.arbiter, lease.arbiter_accepted = arbiter, False
    lease.tenant_approved_arbiter = lease.landlord_approved_arbiter = False
    setattr(lease, f"{actor_role}_approved_arbiter", True)
    lease.save()
    lease.invites.filter(role=Invite.Role.ARBITER, revoked=False).update(revoked=True)
    Invite.objects.create(lease=lease, role=Invite.Role.ARBITER)
    lifecycle.record(lease, "arbiter_named", f"{actor.label} proposed {arbiter.label} as arbiter")


def change_arbiter(lease, party, address):
    role = lease.role_of(party)
    if role not in SIDES:
        raise InvalidTransition("Only the tenant or the landlord can choose the arbiter.", "not_allowed")
    with transaction.atomic():
        lease = Lease.objects.select_for_update().get(pk=lease.pk)
        _require_pending(lease, "change the arbiter")
        _name_arbiter(lease, party, role, address)
    return lease


def approve_arbiter(lease, party):
    role = lease.role_of(party)
    if role not in SIDES:
        raise InvalidTransition("Only the tenant or the landlord approves the arbiter.", "not_allowed")
    with transaction.atomic():
        lease = Lease.objects.select_for_update().get(pk=lease.pk)
        _require_pending(lease, "approve the arbiter")
        if not lease.arbiter_id:
            raise InvalidTransition("No arbiter has been named yet.", "no_arbiter")
        setattr(lease, f"{role}_approved_arbiter", True)
        lease.save(update_fields=[f"{role}_approved_arbiter", "updated_at"])
        lifecycle.record(lease, "arbiter_approved", f"{party.label} ({role}) approved {lease.arbiter.label} as arbiter")
    _maybe_start(lease)
    return lease


def cancel_by(lease, party):
    if lease.created_by_id != party.pk:
        raise InvalidTransition("Only the person who started this agreement can cancel it.", "not_allowed")
    if not lifecycle.cancel(lease, f"{party.label} cancelled the agreement."):
        raise InvalidTransition("This agreement can't be cancelled any more.", "wrong_status")
    lease.refresh_from_db()
    return lease


def _maybe_start(lease):
    lease.refresh_from_db()
    ready = (
        lease.status == S.PENDING_ACCEPTANCE and lease.tenant_id and lease.landlord_id and lease.arbiter_id
        and lease.arbiter_accepted and lease.tenant_approved_arbiter and lease.landlord_approved_arbiter
    )
    if not ready:
        return
    if lease.cancel_after < timezone.now() + timedelta(seconds=MIN_SECONDS_TO_RETURN):
        lifecycle.cancel(lease, "Everyone accepted, but too close to the lease end to lock the deposit safely.", "expired")
        return
    if lifecycle._advance(lease, S.PENDING_ACCEPTANCE, S.DRAFT):
        lifecycle.record(lease, "all_accepted", "Tenant, landlord and arbiter accepted. Creating the settlement account.")
        lifecycle.start_setup(lease)
```

- [ ] **Step 4: Run the tests**

Run: `.venv\Scripts\python manage.py test core.tests.AgreementTests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/agreements.py core/tests.py
git commit -m "Add three-party agreements with invite links and arbiter approval"
```

---

### Task 6: Xaman signing requests

**Files:**
- Create: `core/signing.py`
- Modify: `core/tests.py`

**Interfaces:**
- Consumes: `ledger.xaman` (Task 1), `lifecycle.register_wallet_escrow`, `lifecycle.add_signature`, `lifecycle.register_wallet_anchor`, `lifecycle.next_unsigned_index`, `services.build_escrow_create`, `services.build_anchor_memo`, `services.get_transaction`.
- Produces:
  - `signing.start_signin() -> XamanRequest`
  - `signing.start_escrow(lease, party) -> XamanRequest`
  - `signing.start_payout(proposal, party) -> XamanRequest | None` (None when the party signed every payout)
  - `signing.start_anchor(item, party) -> XamanRequest`
  - `signing.start_trustline(party, currency) -> XamanRequest`
  - `signing.refresh(req) -> XamanRequest` (status open → signed → done, or rejected / expired / failed)
  - `signing.present(req) -> dict` → `{"request_id", "purpose", "status", "error", "qr", "deeplink", "pushed"}`
  - `signing.VALIDATION_TIMEOUT = timedelta(minutes=3)`

- [ ] **Step 1: Write the failing tests**

Append to `core/tests.py` (add `XamanRequest` to the models import and `from ledger import xaman` near the other ledger imports):

```python
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
        req = signing.start_payout(proposal, self.landlord)
        self.assertEqual(req.payload_index, 1)
        self.signed(hex="BLOB1")
        signing.refresh(req)
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv\Scripts\python manage.py test core.tests.SigningTests`
Expected: ERROR `No module named 'core.signing'`.

- [ ] **Step 3: Write `core/signing.py`**

```python
"""Xaman sign requests: one per user action, finished when Xaman reports it signed.

Flow: start_*() creates the Xaman payload and a XamanRequest row; the browser polls our API, which
calls refresh(). refresh() asks Xaman for the payload; once signed it runs the finisher for the
purpose (register the validated transaction, store the multisignature, create the account).
"""
import logging
import re

from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
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

def refresh(req):
    if req.status == R.OPEN:
        info = xaman.get_payload(req.xaman_uuid)
        if info["status"] == "open":
            return req
        if info["status"] in (R.REJECTED, R.EXPIRED):
            req.status = info["status"]
            req.save(update_fields=["status"])
            return req
        req.result, req.status = info, R.SIGNED
        req.save(update_fields=["result", "status"])
        if req.party_id and info.get("user_token"):
            Party.objects.filter(pk=req.party_id).update(xaman_user_token=info["user_token"])
    if req.status == R.SIGNED:
        try:
            finished = FINISHERS[req.purpose](req)
        except (InvalidInput, InvalidTransition, LedgerError) as exc:
            req.status, req.error = R.FAILED, exc.message[:300]
            req.save(update_fields=["status", "error"])
            return req
        if finished:
            req.status = R.DONE
            req.save(update_fields=["status", "party"])
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
    if timezone.now() - req.created_at > VALIDATION_TIMEOUT:
        raise InvalidInput("The network never validated the transaction. Try again.", "not_validated")
    return None


def _finish_signin(req):
    address = str(req.result.get("account") or "")
    if not is_valid_classic_address(address):
        raise InvalidInput("Xaman didn't report a valid account.", "bad_address")
    party, _ = Party.objects.get_or_create(address=address)
    token = req.result.get("user_token")
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
```

Note: `_start` passes `signers` for every purpose but sign-in, so a different Xaman account can't sign.

- [ ] **Step 4: Run the tests**

Run: `.venv\Scripts\python manage.py test core.tests.SigningTests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/signing.py core/tests.py
git commit -m "Add Xaman sign requests for sign-in, lock, approvals, evidence and trust lines"
```

---

### Task 7: Testnet faucet

**Files:**
- Create: `core/faucet.py`
- Modify: `core/tests.py`

**Interfaces:**
- Consumes: `services.account_exists`, `services.get_balances`, `services.has_trust_line`, `services.fund_xrp`, `services.issue_tokens`, `services.faucet_top_up`, `lifecycle.platform_wallet`.
- Produces:
  - `faucet.ACTIVATE_XRP = Decimal("50")`, `faucet.TOKEN_GRANT = Decimal("5000")`, `faucet.COOLDOWN = timedelta(hours=24)`
  - `faucet.status(party) -> dict` → `{"activated": bool, "balances": {code: str}, "trust_lines": {code: bool}, "available": {"activate": bool, "tokens:EUR": bool, ...}}`
  - `faucet.activate(party) -> str` (tx hash)
  - `faucet.grant_tokens(party, currency) -> str` (tx hash)

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv\Scripts\python manage.py test core.tests.FaucetTests`
Expected: ERROR `No module named 'core.faucet'`.

- [ ] **Step 3: Write `core/faucet.py`**

```python
"""Testnet-only faucet: activation XRP and test tokens, each once per account per 24 hours."""
import threading
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone
from xrpl.wallet import Wallet

from ledger import services

from . import lifecycle
from .lifecycle import InvalidTransition
from .models import Currency, FaucetGrant, PlatformWallet

ACTIVATE_XRP = Decimal("50")
TOKEN_GRANT = Decimal("5000")
COOLDOWN = timedelta(hours=24)
TREASURY_KEEP_XRP = Decimal("3")
_guard = threading.Lock()


def _used_recently(party, kind):
    return FaucetGrant.objects.filter(party=party, kind=kind, created_at__gte=timezone.now() - COOLDOWN).exists()


def _require_fresh(party, kind):
    if _used_recently(party, kind):
        raise InvalidTransition("You can use this once every 24 hours.", "too_soon")


def status(party):
    activated = services.account_exists(party.address)
    balances = {code: lifecycle.plain(value) for code, value in services.get_balances(party.address).items()} if activated else {}
    currencies = list(Currency.objects.order_by("code"))
    lines = {c.code: activated and services.has_trust_line(party.address, c.code, c.issuer_address) for c in currencies}
    available = {"activate": not activated and not _used_recently(party, "activate")}
    for c in currencies:
        available[f"tokens:{c.code}"] = lines[c.code] and not _used_recently(party, f"tokens:{c.code}")
    return {"activated": activated, "balances": balances, "trust_lines": lines, "available": available}


def activate(party):
    with _guard:
        _require_fresh(party, "activate")
        if services.account_exists(party.address):
            raise InvalidTransition("Your account is already active on the ledger.", "already_active")
        treasury = lifecycle.platform_wallet("treasury")
        if services.get_balances(treasury.address)["XRP"] < ACTIVATE_XRP + TREASURY_KEEP_XRP:
            services.faucet_top_up(treasury)
        tx_hash = services.fund_xrp(treasury, party.address, ACTIVATE_XRP)
        FaucetGrant.objects.create(party=party, kind="activate")
    cache.delete(f"balances:{party.address}")
    return tx_hash


def grant_tokens(party, currency):
    kind = f"tokens:{currency.code}"
    with _guard:
        _require_fresh(party, kind)
        if not services.has_trust_line(party.address, currency.code, currency.issuer_address):
            raise InvalidTransition(f"Add the {currency.code} trust line in Xaman first.", "no_trust_line")
        issuer = PlatformWallet.objects.filter(address=currency.issuer_address).first()
        if issuer is None:
            raise InvalidTransition("This server doesn't hold that token's issuer. Run bootstrap.", "not_bootstrapped")
        tx_hash = services.issue_tokens(Wallet.from_seed(issuer.seed), party.address, currency.code, TOKEN_GRANT)
        FaucetGrant.objects.create(party=party, kind=kind)
    cache.delete(f"balances:{party.address}")
    return tx_hash
```

- [ ] **Step 4: Run the tests**

Run: `.venv\Scripts\python manage.py test core.tests.FaucetTests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/faucet.py core/tests.py
git commit -m "Add the Testnet faucet with a 24-hour limit"
```

---

### Task 8: JSON API, pages and routes

**Files:**
- Modify: `core/api.py`, `core/views.py`, `core/urls.py`, `config/urls.py`, `core/tests.py`

**Interfaces:**
- Consumes: Tasks 4–7.
- Produces (HTTP; JSON; CSRF on POSTs; errors `{error, code}`; 401 `signed_out` when not signed in):

| Method & path | Body | Result |
|---|---|---|
| `GET /api/me/` | | `{"signed_in": false}` or `{"signed_in": true, "party_id", "name", "address", "explorer_url", "needs_name", "network"}` |
| `POST /api/me/` | `{display_name}` | same as GET |
| `POST /api/auth/signin/` | | Xaman request (`signing.present`) |
| `POST /api/auth/signout/` | | `{"ok": true}` |
| `GET /api/xaman/requests/<uuid>/` | | `signing.present(...)` + on sign-in done: `{"signed_in": true, "needs_name"}` |
| `POST /api/xaman/requests/<uuid>/cancel/` | | `signing.present(...)` |
| `GET /api/faucet/` | | `faucet.status` |
| `POST /api/faucet/activate/` | | `{"tx_hash", "explorer_url"}` |
| `POST /api/faucet/trustline/` | `{currency}` | Xaman request |
| `POST /api/faucet/tokens/` | `{currency}` | `{"tx_hash", "explorer_url"}` |
| `GET /api/leases/` | `?status=` | `{"leases": [...]}` |
| `POST /api/leases/` | `{role, title, currency, amount, end_date or end_in_minutes, grace_minutes, arbiter_address?}` | 201 `{"lease_id"}` |
| `GET /api/leases/<id>/` | | lease state (+ `invites`, `agreement`) |
| `POST /api/leases/<id>/cancel/` | | lease state |
| `POST /api/leases/<id>/arbiter/` | `{address}` | lease state |
| `POST /api/leases/<id>/approve-arbiter/` | | lease state |
| `POST /api/leases/<id>/escrow/` | | Xaman request |
| `POST /api/leases/<id>/release/` | `{reason}` | lease state |
| `POST /api/leases/<id>/proposals/` | `{tenant_amount, landlord_amount, reason}` | 201 `{"proposal_id", "lease"}` |
| `POST /api/proposals/<id>/sign/` | | Xaman request, or `{"done": true, "lease"}` |
| `POST /api/proposals/<id>/retry/` | | `{"lease"}` |
| `POST /api/leases/<id>/evidence/` | multipart `file`, `kind` | 201 evidence |
| `POST /api/evidence/<id>/anchor/` | | Xaman request |
| `GET /api/invites/<token>/` | | `{"role", "status", "lease": {...terms...}, "signed_in", "already_party"}` |
| `POST /api/invites/<token>/accept/` | | `{"lease_id"}` |
| `POST /api/invites/<token>/decline/` | | `{"ok": true}` |
| unchanged | | `GET /api/leases/<id>/public/` (no credential fields), `GET /api/leases/<id>/settlement/`, `GET /api/evidence/<id>/file/` |

Pages: `/` (home, public), `/leases/`, `/leases/new/`, `/leases/<id>/` (sign-in required → redirect `/?next=<path>`), `/invite/<token>/`, `/verify/<id>/`, `/legal/*` (public). Removed: `/demo/`, `/demo/stage/`, `/arbiter/`.

- [ ] **Step 1: Write the failing API tests**

Append to `core/tests.py`:

```python
class ApiTests(LedgerMockedTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(xaman, "create_payload", return_value={"uuid": "x-1", "deeplink": "https://xumm.app/sign/x-1", "pushed": False}).start()
        self.get_payload = mock.patch.object(xaman, "get_payload").start()
        self.get_payload.return_value = {"status": "open", "account": "", "hex": "", "txid": "", "user_token": ""}

    def login(self, party):
        session = self.client.session
        session["party_id"] = party.pk
        session.save()

    def post(self, path, data=None):
        return self.client.post(path, json.dumps(data or {}), content_type="application/json")

    def test_signed_out_visitors(self):
        self.assertEqual(self.client.get("/api/me/").json(), {"signed_in": False})
        response = self.client.get("/api/leases/")
        self.assertEqual((response.status_code, response.json()["code"]), (401, "signed_out"))
        page = self.client.get("/leases/")
        self.assertEqual((page.status_code, page["Location"]), (302, "/?next=/leases/"))
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_signin_flow_logs_in_only_the_browser_that_started_it(self):
        started = self.post("/api/auth/signin/").json()
        self.assertTrue(started["qr"].startswith("data:image/svg+xml"))
        address = Wallet.create().address
        self.get_payload.return_value = {"status": "signed", "account": address, "hex": "", "txid": "", "user_token": ""}
        other = self.client_class()
        self.assertEqual(other.get(f"/api/xaman/requests/{started['request_id']}/").status_code, 404)
        done = self.client.get(f"/api/xaman/requests/{started['request_id']}/").json()
        self.assertEqual((done["status"], done["signed_in"], done["needs_name"]), ("done", True, True))
        me = self.client.get("/api/me/").json()
        self.assertEqual((me["address"], me["needs_name"]), (address, True))
        self.post("/api/me/", {"display_name": "  Giulia   R. "})
        self.assertEqual(self.client.get("/api/me/").json()["name"], "Giulia R.")
        self.post("/api/auth/signout/")
        self.assertEqual(self.client.get("/api/me/").json(), {"signed_in": False})

    def test_csrf_is_enforced_on_posts(self):
        client = self.client_class(enforce_csrf_checks=True)
        self.assertEqual(client.post("/api/auth/signin/", "{}", content_type="application/json").status_code, 403)

    def test_create_agreement_and_accept_by_invite(self):
        self.login(self.tenant)
        created = self.post("/api/leases/", {"role": "tenant", "title": "Flat 3", "currency": "EUR", "amount": "1000",
                                             "end_date": (timezone.now() + timedelta(days=60)).date().isoformat(), "grace_minutes": 60,
                                             "arbiter_address": self.arbiter.address})
        self.assertEqual(created.status_code, 201)
        lease_id = created.json()["lease_id"]
        state = self.client.get(f"/api/leases/{lease_id}/").json()
        self.assertEqual(state["status"], "PENDING_ACCEPTANCE")
        invites = {i["role"]: i for i in state["invites"]}
        self.assertEqual(set(invites), {"landlord", "arbiter"})
        token = invites["landlord"]["url"].rstrip("/").rsplit("/", 1)[-1]

        self.login(self.landlord)
        self.assertEqual(self.client.get(f"/api/leases/{lease_id}/").status_code, 404)  # not a party yet
        terms = self.client.get(f"/api/invites/{token}/").json()
        self.assertEqual((terms["role"], terms["lease"]["amount"]), ("landlord", "1000"))
        self.assertEqual(self.post(f"/api/invites/{token}/accept/").json(), {"lease_id": lease_id})
        landlord_view = self.client.get(f"/api/leases/{lease_id}/").json()
        self.assertEqual(landlord_view["role"], "landlord")

    def test_create_agreement_validates_input(self):
        self.login(self.tenant)
        base = {"role": "tenant", "currency": "EUR", "amount": "1000", "end_in_minutes": 120, "grace_minutes": 5}
        for change, code in (({"role": "arbiter"}, "bad_role"), ({"currency": "GBP"}, "bad_currency"),
                             ({"amount": "0"}, "bad_amount"), ({"end_in_minutes": 1, "grace_minutes": 0}, "bad_end"),
                             ({"arbiter_address": "nope"}, "bad_address")):
            with self.subTest(code=code):
                response = self.post("/api/leases/", {**base, **change})
                self.assertEqual((response.status_code, response.json()["code"]), (400, code))

    def test_lock_starts_a_xaman_request_for_the_tenant_only(self):
        lease = self.make_lease(S.SETTLEMENT_READY)
        self.ledger.build_escrow_create.return_value = {"TransactionType": "EscrowCreate", "Account": self.tenant.address}
        self.login(self.landlord)
        self.assertEqual(self.post(f"/api/leases/{lease.pk}/escrow/").status_code, 409)
        self.login(self.tenant)
        started = self.post(f"/api/leases/{lease.pk}/escrow/").json()
        self.assertEqual((started["purpose"], started["status"]), ("escrow_create", "open"))
        self.login(self.landlord)
        self.assertEqual(self.client.get(f"/api/xaman/requests/{started['request_id']}/").status_code, 404)

    def test_release_is_submitted_by_the_bot(self):
        lease = self.make_lease(S.ACTIVE)
        self.login(self.tenant)
        state = self.post(f"/api/leases/{lease.pk}/release/", {"reason": "early_move_out"}).json()
        self.assertEqual(state["status"], "IN_SETTLEMENT")
        self.assertEqual(self.ledger.finish_escrow.call_args.args[0].address, PlatformWallet.objects.get(role="bot").address)

    def test_proposal_sign_walks_through_payouts_then_reports_done(self):
        lease = self.make_lease(S.IN_SETTLEMENT)
        self.login(self.landlord)
        proposal_id = self.post(f"/api/leases/{lease.pk}/proposals/", {"tenant_amount": "850", "landlord_amount": "150"}).json()["proposal_id"]
        first = self.post(f"/api/proposals/{proposal_id}/sign/").json()
        self.assertEqual(first["purpose"], "payout_sign")
        proposal = SettlementProposal.objects.get(pk=proposal_id)
        lifecycle.add_signature(proposal, self.landlord, 0, "B0")
        lifecycle.add_signature(proposal, self.landlord, 1, "B1")
        self.assertTrue(self.post(f"/api/proposals/{proposal_id}/sign/").json()["done"])

    def test_secrets_never_appear_in_lease_json_or_pages(self):
        lease = self.make_lease(S.ACTIVE)
        self.login(self.tenant)
        body = self.client.get(f"/api/leases/{lease.pk}/").content.decode() + self.client.get(f"/leases/{lease.pk}/").content.decode()
        for secret in (lease.fulfillment_hex, lease.settlement_seed or "sSettlementSeed", PlatformWallet.objects.get(role="bot").seed):
            self.assertNotIn(secret, body)

    def test_faucet_endpoints(self):
        self.login(self.tenant)
        self.ledger.account_exists.return_value = False
        self.ledger.get_balances.return_value = {"XRP": Decimal("500")}
        self.ledger.fund_xrp.return_value = "FUNDHASH"
        self.assertEqual(self.post("/api/faucet/activate/").json()["tx_hash"], "FUNDHASH")
        self.assertEqual(self.post("/api/faucet/trustline/", {"currency": "EUR"}).json()["purpose"], "trustset")
        self.assertEqual(self.post("/api/faucet/tokens/", {"currency": "XXX"}).json()["code"], "bad_currency")

    def test_pages_render_with_a_strict_csp(self):
        lease = self.make_lease(S.ACTIVE)
        self.login(self.tenant)
        for path in ("/", "/leases/", "/leases/new/", f"/leases/{lease.pk}/", f"/verify/{lease.pk}/", "/legal/terms/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("script-src 'self'", response["Content-Security-Policy"])
        for gone in ("/demo/", "/demo/stage/", "/arbiter/", "/api/demo/personas/"):
            self.assertEqual(self.client.get(gone).status_code, 404)
```

Keep the existing evidence upload tests (`test_evidence_upload_checks_the_file`, `test_wallet_anchor_must_carry_this_photos_fingerprint`) that were in the deleted `ApiTests` class? They were deleted in Task 3; re-add this upload test to `ApiTests`:

```python
    def test_evidence_upload_checks_the_file(self):
        lease = self.make_lease(S.ACTIVE)
        self.login(self.tenant)
        from django.core.files.uploadedfile import SimpleUploadedFile
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
        bad = self.client.post(f"/api/leases/{lease.pk}/evidence/", {"kind": "damage", "file": SimpleUploadedFile("x.png", b"GIF89a")})
        self.assertEqual(bad.json()["code"], "bad_file_type")
        with self.settings(MEDIA_ROOT=self._media()):
            ok = self.client.post(f"/api/leases/{lease.pk}/evidence/", {"kind": "damage", "file": SimpleUploadedFile("x.png", png)})
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.json()["anchor_tx_hash"], "")

    def _media(self):
        import tempfile, shutil
        path = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv\Scripts\python manage.py test core.tests.ApiTests`
Expected: ERRORs (old API imports removed names / missing endpoints).

- [ ] **Step 3: Rewrite `core/api.py`**

Keep unchanged: module docstring (edit the "Secrets" paragraph to: "The fulfillment and every seed stay on the server."), `_error`, `_body`, `_int`, `_balances`, `_live`, `_event`, `_evidence`, `_parse_end`, `MAX_EVIDENCE_BYTES`, `EVIDENCE_TYPES`, `EVIDENCE_CONTENT_TYPES`, `SERVER_SIDE_CODES`, `lease_public` (minus credential fields), `evidence` upload (minus the demo anchoring block), `_evidence_for`, `evidence_file`, `settlement_raw`.

Delete: `FUND_*`, `ARBITER_CREDENTIAL`, `CREDENTIAL_TTL`, `CHALLENGE_TTL`, `MIN_SECONDS_TO_RETURN`, `_require_demo`, `credential_issuer_address`, `verified_arbiter`, `me` (old), `demo_personas`, `demo_persona`, `demo_fund`, `demo_run_bot`, `_challenge_text`, `wallet_challenge`, `_verify_wallet_signature`, `wallet_link`, `wallet_unlink`, `_wallet_name`, `credentials`, `_pick_party`, `_create_lease`, `escrow_tx`, `release_tx`, `evidence_anchor_tx`, `STAGE_STEPS`, `_stage_state`, `demo_stage`, `demo_stage_act`, `_is_uuid`. Remove unused imports (`keypairs`, `secrets`, `clock`, `settings` if unused, `PlatformWallet` if unused).

Imports become:

```python
import hashlib
import json
import logging
import uuid
from datetime import datetime, time, timedelta
from functools import wraps

from django.core.cache import cache
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from ledger import services, xaman
from ledger.client import explorer_account, explorer_tx, network, network_key
from ledger.errors import LedgerError

from . import agreements, faucet, lifecycle, signing
from .lifecycle import InvalidInput, InvalidTransition, plain
from .models import Currency, Evidence, Invite, Lease, Party, SettlementProposal, XamanRequest
```

Add the 401 exception and handler (in the `api` decorator, before `except InvalidTransition`):

```python
class NotSignedIn(Exception):
    message, code = "Sign in with Xaman first.", "signed_out"
```

```python
            except NotSignedIn as exc:
                return _error(exc.message, exc.code, 401)
```

Replace `current_party`:

```python
def current_party(request, required=True):
    """The signed-in account of this browser session (sign-in with Xaman), or None / 401."""
    party_id = request.session.get("party_id")
    party = Party.objects.filter(pk=party_id).first() if party_id else None
    if party is None and required:
        raise NotSignedIn()
    return party
```

`_lease_for` stays. `_require_action` message: `"That isn't possible right now for your role in this deposit."`.

Replace `_party`, `_proposal` (only `proposed_by`/`signed_by`/`still_needed` builders change), `_lease_summary`, `lease_state`:

```python
ROLES = ("tenant", "landlord", "arbiter")


def _party(party, role=None):
    return {"party_id": party.pk, "name": party.label, "role": role, "address": party.address,
            "explorer_url": explorer_account(party.address)}


def _parties(lease):
    return [_party(p, role) for role, p in zip(ROLES, lease.signers()) if p is not None]


def _proposal(proposal, lease):
    complete = set(lifecycle.complete_signers(proposal))
    signers = [(role, p) for role, p in zip(ROLES, lease.signers())]
    is_open = proposal.status == SettlementProposal.Status.OPEN
    return {
        "id": proposal.pk, "status": proposal.status,
        "tenant_amount": plain(proposal.tenant_amount), "landlord_amount": plain(proposal.landlord_amount),
        "reason": proposal.reason,
        "proposed_by": _party(proposal.proposed_by, lease.role_of(proposal.proposed_by)),
        "signed_by": [_party(p, role) for role, p in signers if str(p.pk) in complete],
        "still_needed": [_party(p, role) for role, p in signers if str(p.pk) not in complete] if is_open else [],
        "signatures_count": len(complete), "signatures_required": lifecycle.QUORUM,
        "payouts_count": len(proposal.prepared_txs),
        "executed": [{"tx_hash": h, "explorer_url": explorer_tx(h)} for h in proposal.executed_hashes],
        "created_at": proposal.created_at.isoformat(),
    }


def _lease_summary(lease, party):
    return {
        "id": str(lease.pk), "title": lease.title,
        "status": lease.status, "status_label": lease.get_status_display(),
        "setup_step": lease.setup_step, "release_reason": lease.release_reason, "error": lease.error,
        "amount": plain(lease.amount), "currency": lease.currency.code, "currency_label": lease.currency.label,
        "role": lease.role_of(party), "parties": _parties(lease),
        "open_roles": [role for role, p in zip(ROLES, lease.signers()) if p is None],
        "end_at": lease.end_at.isoformat(), "grace_minutes": lease.grace_minutes,
        "cancel_after": lease.cancel_after.isoformat(), "created_at": lease.created_at.isoformat(),
    }


def _agreement(lease, party, request):
    role = lease.role_of(party)
    data = {
        "arbiter_accepted": lease.arbiter_accepted,
        "tenant_approved_arbiter": lease.tenant_approved_arbiter,
        "landlord_approved_arbiter": lease.landlord_approved_arbiter,
        "created_by": lease.created_by_id,
    }
    invites = []
    if lease.status == Lease.Status.PENDING_ACCEPTANCE and role in ("tenant", "landlord"):
        invites = [{"role": i.role, "url": request.build_absolute_uri(f"/invite/{i.token}/")} for i in agreements.open_invites(lease)]
    return data, invites


def lease_state(lease, party, request):
    """Everything the lease page shows. Never includes the fulfillment or any seed."""
    summary = _lease_summary(lease, party)
    agreement, invites = _agreement(lease, party, request)
    base = {**summary, "agreement": agreement, "invites": invites, "issuer_address": lease.currency.issuer_address,
            "network": network_key(), "me": _party(party, lease.role_of(party)),
            "events": [_event(e) for e in lease.events.all()], "actions": lifecycle.allowed_actions(lease, party)}
    if lease.status in (Lease.Status.PENDING_ACCEPTANCE, Lease.Status.CANCELLED) and not lease.settlement_address:
        return {**base, "settlement": None, "escrow": None, "seconds_to_cancel_after": None, "proposals": [], "evidence": []}
    now = lifecycle.ledger_now()
    live = _live(lease)
    escrow = None
    if lease.escrow_sequence is not None:
        escrow = {
            "owner": lease.escrow_owner, "sequence": lease.escrow_sequence, "destination": lease.settlement_address,
            "cancel_after": lease.cancel_after.isoformat(), "exists": live["escrow_exists"],
            "explorer_url": explorer_account(lease.escrow_owner),
        }
    return {
        **base, "actions": lifecycle.allowed_actions(lease, party, now),
        "settlement": live["settlement"], "escrow": escrow,
        "seconds_to_cancel_after": max(0, int((lease.cancel_after - now).total_seconds())),
        "proposals": [_proposal(p, lease) for p in lease.proposals.select_related("proposed_by")],
        "evidence": [_evidence(e) for e in lease.evidence.select_related("uploaded_by")],
    }
```

`_live`: the `roles` dict uses `p.label` instead of `p.display_name`.

Every call `lease_state(lease, party)` becomes `lease_state(lease, party, request)`.

Accounts and Xaman endpoints:

```python
def _me(party):
    if party is None:
        return {"signed_in": False}
    return {"signed_in": True, **_party(party), "needs_name": not party.display_name, "network": network_key()}


@api("GET", "POST")
def me(request):
    party = current_party(request, required=request.method == "POST")
    if request.method == "POST":
        name = " ".join(str(_body(request).get("display_name") or "").split())
        if not 1 <= len(name) <= 80:
            raise InvalidInput("Give a name of 1 to 80 characters.", "bad_name")
        party.display_name = name
        party.save(update_fields=["display_name"])
    return _me(party)


@api("POST")
def signin(request):
    req = signing.start_signin()
    request.session["signin_request"] = str(req.pk)  # only this browser may complete it
    return signing.present(req)


@api("POST")
def signout(request):
    request.session.flush()
    return {"ok": True}


def _request_for(request, request_id):
    req = XamanRequest.objects.select_related("party", "lease", "proposal", "evidence").filter(pk=request_id).first()
    if req is None:
        raise Http404
    if req.purpose == XamanRequest.Purpose.SIGNIN:
        if request.session.get("signin_request") != str(req.pk):
            raise Http404
    elif req.party_id != request.session.get("party_id"):
        raise Http404
    return req


@api("GET")
def xaman_request(request, request_id):
    req = signing.refresh(_request_for(request, request_id))
    data = signing.present(req)
    if req.purpose == XamanRequest.Purpose.SIGNIN and req.status == XamanRequest.Status.DONE:
        request.session.cycle_key()  # new session id once the identity changes (session fixation)
        request.session["party_id"] = req.party_id
        request.session.pop("signin_request", None)
        data.update(signed_in=True, needs_name=not req.party.display_name)
    return data


@api("POST")
def xaman_cancel(request, request_id):
    req = _request_for(request, request_id)
    if req.status == XamanRequest.Status.OPEN:
        xaman.cancel_payload(req.xaman_uuid)
        req.status = XamanRequest.Status.REJECTED
        req.save(update_fields=["status"])
    return signing.present(req)
```

Faucet endpoints:

```python
def _currency(data):
    currency = Currency.objects.filter(code=str(data.get("currency") or "").upper()).first()
    if currency is None:
        raise InvalidInput("Pick EUR or USD.", "bad_currency")
    return currency


@api("GET")
def faucet_status(request):
    return faucet.status(current_party(request))


@api("POST")
def faucet_activate(request):
    tx_hash = faucet.activate(current_party(request))
    return {"tx_hash": tx_hash, "explorer_url": explorer_tx(tx_hash)}


@api("POST")
def faucet_trustline(request):
    party = current_party(request)
    return signing.present(signing.start_trustline(party, _currency(_body(request))))


@api("POST")
def faucet_tokens(request):
    party = current_party(request)
    tx_hash = faucet.grant_tokens(party, _currency(_body(request)))
    return {"tx_hash": tx_hash, "explorer_url": explorer_tx(tx_hash)}
```

Leases and agreements:

```python
@api("GET", "POST")
def leases(request):
    party = current_party(request)
    if request.method == "POST":
        data = _body(request)
        currency = _currency(data)
        amount = lifecycle.parse_amount(data.get("amount"))
        if amount <= 0:
            raise InvalidInput("The deposit must be greater than zero.", "bad_amount")
        services.token_value(amount)
        title = " ".join(str(data.get("title") or "").split())
        if len(title) > 120:
            raise InvalidInput("Keep the property name under 120 characters.", "bad_title")
        lease = agreements.create_draft(
            party, data.get("role"), currency=currency, amount=amount, end_at=_parse_end(data),
            grace_minutes=_int(data.get("grace_minutes", 1440), "grace_minutes", 0, 60 * 24 * 365),
            title=title, arbiter_address=str(data.get("arbiter_address") or "").strip(),
        )
        return JsonResponse({"lease_id": str(lease.pk)}, status=201)
    found = Lease.objects.filter(Q(tenant=party) | Q(landlord=party) | Q(arbiter=party)).select_related("tenant", "landlord", "arbiter", "currency")
    status = request.GET.get("status")
    if status:
        wanted = [s for s in status.upper().split(",") if s in Lease.Status.values]
        if not wanted:
            raise InvalidInput("Unknown status filter.", "bad_status")
        found = found.filter(status__in=wanted)
    return {"leases": [_lease_summary(lease, party) for lease in found[:200]]}
```

Order matters in `create_draft` validation: `bad_role` must be raised before `bad_end`; `_parse_end` raises `bad_end` for malformed dates first, which is fine because the role test supplies a valid date. Validate the role before parsing the end date:

```python
        if data.get("role") not in ("tenant", "landlord"):
            raise InvalidInput("Choose whether you are the tenant or the landlord.", "bad_role")
```

(put it first inside the `POST` branch). Put the `arbiter_address` format check before `create_draft` too, so a bad address doesn't create a half-made draft:

```python
        arbiter_address = str(data.get("arbiter_address") or "").strip()
        if arbiter_address and not is_valid_classic_address(arbiter_address):
            raise InvalidInput("The arbiter's address is not a valid XRP Ledger address.", "bad_address")
```

(add `from xrpl.core.addresscodec import is_valid_classic_address` to the imports and pass `arbiter_address=arbiter_address`).

```python
@api("GET")
def lease_detail(request, lease_id):
    party = current_party(request)
    return lease_state(_lease_for(lease_id, party), party, request)


@api("POST")
def lease_cancel(request, lease_id):
    party = current_party(request)
    lease = agreements.cancel_by(_lease_for(lease_id, party), party)
    return lease_state(lease, party, request)


@api("POST")
def lease_arbiter(request, lease_id):
    party = current_party(request)
    lease = agreements.change_arbiter(_lease_for(lease_id, party), party, _body(request).get("address"))
    return lease_state(lease, party, request)


@api("POST")
def lease_approve_arbiter(request, lease_id):
    party = current_party(request)
    lease = agreements.approve_arbiter(_lease_for(lease_id, party), party)
    return lease_state(_lease_for(lease.pk, party), party, request)


@api("POST")
def escrow(request, lease_id):
    party = current_party(request)
    return signing.present(signing.start_escrow(_lease_for(lease_id, party), party))


@api("POST")
def release(request, lease_id):
    party = current_party(request)
    lease = _lease_for(lease_id, party)
    reason = _body(request).get("reason")
    if reason not in Lease.ReleaseReason.values:
        raise InvalidInput("The reason must be early_move_out or damage_claim.", "bad_reason")
    _require_action(lease, party, "release_early" if reason == Lease.ReleaseReason.EARLY_MOVE_OUT else "claim_damage")
    lifecycle.release(lease, party, reason)
    return lease_state(lease, party, request)


@api("POST")
def proposals(request, lease_id):
    party = current_party(request)
    lease = _lease_for(lease_id, party)
    data = _body(request)
    _require_action(lease, party, "propose")
    proposal = lifecycle.propose(lease, party, data.get("tenant_amount"), data.get("landlord_amount"), data.get("reason", ""))
    return JsonResponse({"proposal_id": proposal.pk, "lease": lease_state(lease, party, request)}, status=201)


def _proposal_for(proposal_id, party):
    proposal = SettlementProposal.objects.select_related("lease").filter(pk=proposal_id).first()
    if proposal is None:
        raise Http404
    _lease_for(proposal.lease_id, party)
    return proposal


@api("POST")
def proposal_sign(request, proposal_id):
    party = current_party(request)
    proposal = _proposal_for(proposal_id, party)
    req = signing.start_payout(proposal, party)
    if req is None:
        return {"done": True, "lease": lease_state(_lease_for(proposal.lease_id, party), party, request)}
    return signing.present(req)


@api("POST")
def proposal_retry(request, proposal_id):
    party = current_party(request)
    proposal = lifecycle.retry_payout(_proposal_for(proposal_id, party), party)
    return {"lease": lease_state(_lease_for(proposal.lease_id, party), party, request)}


@api("POST")
def evidence_anchor(request, evidence_id):
    party = current_party(request)
    return signing.present(signing.start_anchor(_evidence_for(evidence_id, party), party))
```

In `evidence` (upload): delete the `if party.seed and settings.DEMO_MODE:` block; return `JsonResponse(_evidence(item), status=201)`. The browser then calls `/api/evidence/<id>/anchor/`.

Invites:

```python
@api("GET")
def invite(request, token):
    item = agreements.invite_for(token)
    lease, party = item.lease, current_party(request, required=False)
    return {
        "role": item.role, "used": bool(item.accepted_by_id or item.declined_at),
        "accepted_by_you": bool(party and item.accepted_by_id == party.pk),
        "signed_in": party is not None, "already_party": bool(party and lease.role_of(party) and item.accepted_by_id != party.pk),
        "lease": {
            "id": str(lease.pk), "title": lease.title, "status": lease.status, "status_label": lease.get_status_display(),
            "amount": plain(lease.amount), "currency": lease.currency.code, "currency_label": lease.currency.label,
            "end_at": lease.end_at.isoformat(), "grace_minutes": lease.grace_minutes, "cancel_after": lease.cancel_after.isoformat(),
            "parties": _parties(lease), "created_by": lease.created_by.label,
        },
    }


@api("POST")
def invite_accept(request, token):
    lease = agreements.accept_invite(token, current_party(request))
    return {"lease_id": str(lease.pk)}


@api("POST")
def invite_decline(request, token):
    agreements.decline_invite(token, current_party(request))
    return {"ok": True}
```

`lease_public`: replace `lease.tenant.address` etc. with `getattr(lease.tenant, "address", "")` (same for landlord, arbiter) and delete `"credential_registry"` and `"arbiter_credential"`. Return 404 for `PENDING_ACCEPTANCE` leases (`if lease is None or lease.status == Lease.Status.PENDING_ACCEPTANCE: raise Http404`).

- [ ] **Step 4: Update `core/views.py`**

```python
"""HTML pages. Each one is a server-rendered shell; its ES module in static/js/pages/ fills it from the JSON API.

No page receives secrets through the template context. Pages for signed-in accounts redirect to the
home page (which has the Xaman sign-in) when nobody is signed in.
"""
from urllib.parse import quote

from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_safe

from .models import Invite, Lease


def _signed_in(request):
    return bool(request.session.get("party_id"))


def page(template, signed_in=False):
    @require_safe
    @ensure_csrf_cookie  # the page's JS needs the cookie for its first POST
    def view(request):
        if signed_in and not _signed_in(request):
            return redirect(f"/?next={quote(request.get_full_path())}")
        return render(request, template)

    return view


home = page("escrow/home.html")
dashboard = page("escrow/dashboard.html", signed_in=True)
lease_create = page("escrow/lease_create.html", signed_in=True)
terms = page("legal/terms.html")
privacy = page("legal/privacy.html")


@require_safe
@ensure_csrf_cookie
def lease_detail(request, lease_id):
    if not _signed_in(request):
        return redirect(f"/?next={quote(request.get_full_path())}")
    if not Lease.objects.filter(pk=lease_id).exists():
        raise Http404
    return render(request, "escrow/lease_detail.html", {"lease_id": str(lease_id)})


@require_safe
@ensure_csrf_cookie
def verify(request, lease_id):
    if not Lease.objects.filter(pk=lease_id).exclude(status=Lease.Status.PENDING_ACCEPTANCE).exists():
        raise Http404
    return render(request, "escrow/verify.html", {"lease_id": str(lease_id)})


@require_safe
@ensure_csrf_cookie
def invite(request, token):
    if not Invite.objects.filter(token=token, revoked=False).exists():
        raise Http404
    return render(request, "escrow/invite.html", {"token": token})
```

- [ ] **Step 5: Update `core/urls.py`**

```python
"""Routes: the HTML pages (core/views.py) and the JSON API (the contract in core/api.py)."""
from django.urls import path

from . import api, views

escrow_patterns = [
    path("leases/", views.dashboard, name="dashboard"),
    path("leases/new/", views.lease_create, name="lease_create"),
    path("leases/<uuid:lease_id>/", views.lease_detail, name="lease_detail"),
    path("verify/<uuid:lease_id>/", views.verify, name="verify"),
    path("invite/<str:token>/", views.invite, name="invite"),
]

legal_patterns = [
    path("terms/", views.terms, name="terms"),
    path("privacy/", views.privacy, name="privacy"),
]

api_patterns = [
    path("api/me/", api.me),
    path("api/auth/signin/", api.signin),
    path("api/auth/signout/", api.signout),
    path("api/xaman/requests/<uuid:request_id>/", api.xaman_request),
    path("api/xaman/requests/<uuid:request_id>/cancel/", api.xaman_cancel),
    path("api/faucet/", api.faucet_status),
    path("api/faucet/activate/", api.faucet_activate),
    path("api/faucet/trustline/", api.faucet_trustline),
    path("api/faucet/tokens/", api.faucet_tokens),
    path("api/invites/<str:token>/", api.invite),
    path("api/invites/<str:token>/accept/", api.invite_accept),
    path("api/invites/<str:token>/decline/", api.invite_decline),
    path("api/leases/", api.leases),
    path("api/leases/<uuid:lease_id>/", api.lease_detail),
    path("api/leases/<uuid:lease_id>/public/", api.lease_public),
    path("api/leases/<uuid:lease_id>/settlement/", api.settlement_raw),
    path("api/leases/<uuid:lease_id>/cancel/", api.lease_cancel),
    path("api/leases/<uuid:lease_id>/arbiter/", api.lease_arbiter),
    path("api/leases/<uuid:lease_id>/approve-arbiter/", api.lease_approve_arbiter),
    path("api/leases/<uuid:lease_id>/escrow/", api.escrow),
    path("api/leases/<uuid:lease_id>/release/", api.release),
    path("api/leases/<uuid:lease_id>/proposals/", api.proposals),
    path("api/leases/<uuid:lease_id>/evidence/", api.evidence),
    path("api/evidence/<int:evidence_id>/file/", api.evidence_file),
    path("api/evidence/<int:evidence_id>/anchor/", api.evidence_anchor),
    path("api/proposals/<int:proposal_id>/sign/", api.proposal_sign),
    path("api/proposals/<int:proposal_id>/retry/", api.proposal_retry),
]
```

- [ ] **Step 6: Run the full suite**

Run: `.venv\Scripts\python manage.py test`
Expected: PASS. (`test_pages_render_with_a_strict_csp` needs `templates/escrow/invite.html` only for the invite page, which isn't in that list; the templates referenced exist. If a template still uses `{% url 'escrow:demo' %}`, `escrow:arbiter` or `escrow:stage`, rendering fails with `NoReverseMatch` — fix those references in Task 9; to unblock now, replace them in `templates/partials/_header.html` and `templates/escrow/home.html` with `{% url 'escrow:dashboard' %}` and delete the include of `_demo_bar.html` in `base.html`.)

- [ ] **Step 7: Commit**

```bash
git add core config
git commit -m "Rewrite the JSON API and pages for Xaman accounts, agreements and the faucet"
```

---

### Task 9: Frontend — shell, Xaman dialog, identity and actions

**Files:**
- Create: `static/js/xaman.js`
- Modify: `templates/base.html`, `templates/partials/_header.html`, `static/js/session.js`, `static/js/app.js`, `static/js/actions.js`, `static/js/parts.js`, `static/css/global.css`
- Delete: `static/js/wallet.js`, `templates/partials/_demo_bar.html`, `static/vendor/gemwallet-api-3.8.0.js`, `static/vendor/LICENSE-gemwallet-api.txt`

**Interfaces:**
- Produces (JS):
  - `xaman.js`: `export async function signWithXaman(start: () => Promise<object>, { title }) : Promise<object>` — `start` returns the API's Xaman request; resolves with the final request state (`status: "done"`), rejects with `Error` on rejected / expired / failed / closed.
  - `xaman.js`: `export async function signSteps(path: string, { title }) : Promise<object>` — POSTs `path` until the answer has `done: true`, signing each request; resolves with the last API answer.
  - `session.js`: `config`, `loadMe(force)`, `currentMe()`, `onMe(callback)`, `signOut()`; event `me:changed`.
  - `actions.js`: `lockDeposit(leaseId)`, `releaseDeposit(leaseId, reason)`, `proposeSplit(leaseId, tenant, landlord, reason)`, `approveSplit(proposalId)`, `retryPayout(proposalId)`, `uploadEvidence(leaseId, form)`, `anchorEvidence(item)`, `verifyEvidence(item, leaseId, opts)` (unchanged), `addTrustLine(currency)`, `signIn()`.

- [ ] **Step 1: Remove GemWallet and the demo bar**

```bash
git rm static/js/wallet.js templates/partials/_demo_bar.html
git rm --cached static/vendor/gemwallet-api-3.8.0.js 2>nul
del static\vendor\gemwallet-api-3.8.0.js static\vendor\LICENSE-gemwallet-api.txt
```

In `scripts/fetch_vendor.py` delete the `gemwallet-api-3.8.0.js` entry from `FILES` and the GemWallet sentence in the docstring. In `.gitignore` delete the two GemWallet lines.

Move `loadVendor` (needed by `ledger.js`) into `static/js/ledger.js`:

```javascript
const VENDOR = { xrpl: "vendor/xrpl-5.3.0.min.js" };
const loading = new Map();

/** Load a self-hosted classic script once (CSP: script-src 'self'). */
export function loadVendor(name) {
  if (!loading.has(name)) {
    loading.set(name, new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = (document.body.dataset.static || "/static/") + VENDOR[name];
      script.onload = () => resolve();
      script.onerror = () => {
        loading.delete(name);
        reject(new Error(`Couldn't load ${VENDOR[name]}. Run scripts/fetch_vendor.py on the server.`));
      };
      document.head.append(script);
    }));
  }
  return loading.get(name);
}
```

and replace `import { loadVendor } from "./wallet.js";` in `ledger.js` with nothing (the function now lives there).

- [ ] **Step 2: `templates/base.html`**

- `<body class="{% block body_class %}{% endblock %}" data-static="{% static '' %}">` (no `has-demo-bar`).
- Replace the role `<dialog>` with the Xaman dialog:

```html
  {# Xaman sign requests: QR code (rendered by our server) and a deep link for phones. Filled by static/js/xaman.js. #}
  <dialog class="dialog" data-xaman-dialog aria-labelledby="xaman-title">
    <h2 id="xaman-title" data-xaman-title>Sign in Xaman</h2>
    <p class="muted" data-xaman-text>Scan the code with the Xaman app, or open it on this phone.</p>
    <img class="qr" data-xaman-qr alt="QR code for the Xaman sign request" width="240" height="240">
    <p class="actions"><a class="btn btn--primary" data-xaman-link target="_blank" rel="noopener">Open in Xaman</a></p>
    <p class="small" data-xaman-status role="status" aria-live="polite"></p>
    <div class="dialog__foot"><button type="button" class="btn btn--ghost btn--sm" data-xaman-cancel>Cancel</button></div>
  </dialog>

  {# Asked once after the first sign-in. #}
  <dialog class="dialog" data-name-dialog aria-labelledby="name-title">
    <form method="dialog" data-name-form>
      <h2 id="name-title">What should the other parties call you?</h2>
      <p class="muted">Shown to the tenant, landlord or arbiter of your agreements. Never written to the ledger.</p>
      <div class="field mt-4"><label for="id_display_name">Your name</label>
        <input id="id_display_name" name="display_name" maxlength="80" required autocomplete="name"></div>
      <div class="actions mt-4"><button class="btn btn--primary" value="save">Save</button></div>
    </form>
  </dialog>
```

- Delete `{% if escrow_config.demo_mode %}{% include "partials/_demo_bar.html" %}{% endif %}`.

- [ ] **Step 3: `templates/partials/_header.html`**

Replace the `<nav>` list and the `.wallet` contents after the sound toggle:

```html
    <nav class="site-nav" aria-label="Main" data-signed-in-only hidden>
      <ul>
        <li><a href="{% url 'escrow:dashboard' %}"{% if request.resolver_match.url_name == 'dashboard' %} aria-current="page"{% endif %}>My deposits</a></li>
        <li><a href="{% url 'escrow:lease_create' %}"{% if request.resolver_match.url_name == 'lease_create' %} aria-current="page"{% endif %}>New agreement</a></li>
      </ul>
    </nav>
```

```html
      {# Filled from GET /api/me/. #}
      <span class="persona-badge" data-me hidden>
        <span class="persona-badge__name" data-me-name></span>
        <span class="wallet__address mono" data-me-address></span>
      </span>
      <button type="button" class="btn btn--primary btn--sm" data-signin hidden>Sign in with Xaman</button>
      <button type="button" class="btn btn--ghost btn--sm" data-signout hidden>Sign out</button>
```

- [ ] **Step 4: CSS for the QR code**

Append to `static/css/global.css` (section 10, app components):

```css
/* Xaman sign request dialog */
.qr { display: block; width: 240px; height: 240px; margin: 1rem auto; background: #fff; border-radius: 8px; }
.qr[hidden] { display: none; }
```

- [ ] **Step 5: `static/js/xaman.js`**

```javascript
/**
 * Xaman sign requests. The server creates the request and renders its QR code; this module shows
 * it, polls GET /api/xaman/requests/<id>/ until it is done, and reports the outcome.
 * Push notifications reach the phone too once the account has signed in before.
 */
import { get, post } from "./api.js";

const POLL_MS = 2000;
const dialog = () => document.querySelector("[data-xaman-dialog]");
const STATUS_TEXT = {
  open: "Waiting for you to sign in Xaman…",
  signed: "Signed. Waiting for the ledger to confirm…",
  done: "Done.",
};

export class XamanCancelled extends Error {}

function fillDialog(node, request, title) {
  node.querySelector("[data-xaman-title]").textContent = title;
  const qr = node.querySelector("[data-xaman-qr]");
  qr.hidden = !request.qr;
  if (request.qr) qr.src = request.qr;
  const link = node.querySelector("[data-xaman-link]");
  link.hidden = !request.deeplink;
  if (request.deeplink) link.href = request.deeplink;
  node.querySelector("[data-xaman-text]").textContent = request.pushed
    ? "A sign request was sent to your Xaman app. You can also scan the code."
    : "Scan the code with the Xaman app, or open it on this phone.";
  node.querySelector("[data-xaman-status]").textContent = STATUS_TEXT[request.status] || "";
}

/**
 * @param {() => Promise<object>} start  calls the API endpoint that creates the request
 * @param {{title?: string}} [options]
 */
export async function signWithXaman(start, { title = "Sign in Xaman" } = {}) {
  const node = dialog();
  let request = await start();
  let cancelled = false;
  const onCancel = async () => {
    cancelled = true;
    try { await post(`/api/xaman/requests/${request.request_id}/cancel/`, {}, { quiet: true }); } catch { /* already resolved */ }
  };
  const cancelButton = node.querySelector("[data-xaman-cancel]");
  cancelButton.addEventListener("click", onCancel);
  node.addEventListener("cancel", onCancel);
  fillDialog(node, request, title);
  if (!node.open) node.showModal();
  try {
    while (!cancelled && ["open", "signed"].includes(request.status)) {
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      if (cancelled) break;
      request = await get(`/api/xaman/requests/${request.request_id}/`, { quiet: true });
      fillDialog(node, request, title);
    }
  } finally {
    cancelButton.removeEventListener("click", onCancel);
    node.removeEventListener("cancel", onCancel);
    if (node.open) node.close();
  }
  if (cancelled || request.status === "rejected") throw new XamanCancelled("Cancelled in Xaman. Nothing was signed.");
  if (request.status === "expired") throw new XamanCancelled("The sign request expired. Try again.");
  if (request.status === "failed") throw new Error(request.error || "That didn't work. Try again.");
  return request;
}

/** POST `path` repeatedly, signing each Xaman request it returns, until the API answers {done: true}. */
export async function signSteps(path, { title } = {}) {
  for (let step = 0; step < 10; step += 1) {
    const answer = await post(path);
    if (answer.done) return answer;
    await signWithXaman(async () => answer, { title });
  }
  throw new Error("Too many signing steps.");
}
```

- [ ] **Step 6: `static/js/session.js`**

```javascript
/**
 * Who is signed in (GET /api/me/). Fires "me:changed" on document whenever it is loaded.
 */
import { get, post } from "./api.js";

let me = null;
let pending = null;

export function readConfig() {
  const fallback = { network: { id: "testnet", name: "Testnet", is_testnet: true, explorer: "", ws: "" }, xaman_configured: false, signed_in: false, currencies: [] };
  try {
    const parsed = JSON.parse(document.getElementById("escrow-config")?.textContent || "null");
    return parsed && typeof parsed === "object" ? { ...fallback, ...parsed } : fallback;
  } catch {
    return fallback;
  }
}
export const config = readConfig();

export function loadMe(force = false) {
  if (!pending || force) {
    pending = get("/api/me/", { quiet: true }).then((data) => {
      me = data.signed_in ? data : null;
      document.dispatchEvent(new CustomEvent("me:changed", { detail: me }));
      return me;
    }).catch((error) => {
      pending = null;
      throw error;
    });
  }
  return pending;
}

export const currentMe = () => me;

export function onMe(callback) {
  document.addEventListener("me:changed", (event) => callback(event.detail));
}

export async function signOut() {
  await post("/api/auth/signout/");
  window.location.assign("/");
}
```

- [ ] **Step 7: `static/js/actions.js`**

```javascript
/**
 * Lease actions. Every signature happens in the user's Xaman app; the server builds the
 * transaction, Xaman signs it, and the server checks the result on the ledger.
 */
import { api, post } from "./api.js";
import { readMemo, sha256Hex, withLedger, optional } from "./ledger.js";
import { loadMe } from "./session.js";
import { signSteps, signWithXaman } from "./xaman.js";

export const EVIDENCE_MEMO = "safedeposit/evidence";

export async function signIn() {
  const done = await signWithXaman(() => post("/api/auth/signin/"), { title: "Sign in with Xaman" });
  await loadMe(true);
  return done;
}

export async function lockDeposit(leaseId) {
  await signWithXaman(() => post(`/api/leases/${leaseId}/escrow/`), { title: "Lock the deposit" });
}

/** @param {"early_move_out"|"damage_claim"} reason  The bot submits the release; no signature needed. */
export function releaseDeposit(leaseId, reason) {
  return post(`/api/leases/${leaseId}/release/`, { reason });
}

export async function proposeSplit(leaseId, tenantAmount, landlordAmount, reason) {
  const result = await post(`/api/leases/${leaseId}/proposals/`, { tenant_amount: tenantAmount, landlord_amount: landlordAmount, reason });
  return result;
}

export async function approveSplit(proposalId) {
  return (await signSteps(`/api/proposals/${proposalId}/sign/`, { title: "Approve the split" })).lease;
}

export async function retryPayout(proposalId) {
  return (await post(`/api/proposals/${proposalId}/retry/`)).lease;
}

export async function addTrustLine(currency) {
  await signWithXaman(() => post("/api/faucet/trustline/", { currency }), { title: `Add the ${currency} trust line` });
}

export async function uploadEvidence(leaseId, form) {
  const item = await api(`/api/leases/${leaseId}/evidence/`, { method: "POST", form });
  return anchorEvidence(item).then(() => item, (error) => ({ ...item, anchor_error: error.message }));
}

export async function anchorEvidence(item) {
  await signWithXaman(() => post(`/api/evidence/${item.id}/anchor/`), { title: "Anchor the photo" });
}
```

Keep `verifyEvidence` exactly as it is now, below these functions.

- [ ] **Step 8: `static/js/app.js`**

- Imports become:

```javascript
import { signIn } from "./actions.js";
import { initSound } from "./sound.js";
import { initFx } from "./fx.js";
import { post } from "./api.js";
import { config, loadMe, onMe, signOut } from "./session.js";
import { busy, notify, shortAddress, toast } from "./ui.js";

export { config, notify, shortAddress };
```

- Replace everything from `/* ---------- Who am I` through the end of `runBot` with:

```javascript
/* ---------- Who am I: header identity, sign-in and the first-name prompt ---------- */
function renderIdentity(me) {
  const badge = document.querySelector("[data-me]");
  if (badge) {
    badge.hidden = !me;
    if (me) {
      badge.querySelector("[data-me-name]").textContent = me.name;
      badge.querySelector("[data-me-address]").textContent = shortAddress(me.address);
      badge.title = me.address;
    }
  }
  document.querySelectorAll("[data-signed-in-only]").forEach((node) => { node.hidden = !me; });
  const signInButton = document.querySelector("[data-signin]");
  if (signInButton) signInButton.hidden = Boolean(me) || !config.xaman_configured;
  const signOutButton = document.querySelector("[data-signout]");
  if (signOutButton) signOutButton.hidden = !me;
  if (me?.needs_name) askName();
}

function askName() {
  const dialog = document.querySelector("[data-name-dialog]");
  if (!dialog?.showModal || dialog.open) return;
  const form = dialog.querySelector("[data-name-form]");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = form.elements.display_name.value.trim();
    if (!name) return;
    try {
      await post("/api/me/", { display_name: name });
      dialog.close();
      await loadMe(true);
    } catch { /* shown by api.js */ }
  }, { once: true });
  dialog.showModal();
}

export async function startSignIn(button) {
  await busy(button, "Waiting for Xaman…", async () => {
    try {
      await signIn();
      const next = new URLSearchParams(location.search).get("next");
      if (next && next.startsWith("/") && !next.startsWith("//")) location.assign(next);
      else if (location.pathname === "/") location.assign("/leases/");
    } catch (error) {
      if (!error.code) toast(error.message, "warning");
    }
  });
}
```

- In `init()`: replace the two wallet listeners and `onPersona(renderIdentity)` with

```javascript
  document.querySelector("[data-signin]")?.addEventListener("click", (event) => startSignIn(event.currentTarget));
  document.querySelector("[data-signout]")?.addEventListener("click", () => signOut().catch(() => {}));
  onMe(renderIdentity);
```

delete `initDemoBar();`, and replace the last line with `loadMe().catch(() => renderIdentity(null));`.

- [ ] **Step 9: `static/js/parts.js`**

- Delete `verifiedBadge` and its use in `partyCard` (keep `copyButton` and the explorer link in that row).
- In `proposalCard`, make the approve button text Xaman-based:

```javascript
    canApprove && el("div", { class: "actions mt-4" },
      el("button", { type: "button", class: "btn btn--primary", on: { click: (event) => onApprove(event.currentTarget, proposal) } },
        proposal.payouts_count > 1 ? `Approve in Xaman (${proposal.payouts_count} signatures)` : "Approve in Xaman")),
    canRetry && el("div", { class: "actions mt-4" },
      el("button", { type: "button", class: "btn", on: { click: (event) => onRetry(event.currentTarget, proposal) } }, "Retry the payout")));
```

with the signature `export function proposalCard(proposal, state, onApprove, onRetry)` and

```javascript
  const canApprove = onApprove && state.actions.includes(`sign:${proposal.id}`);
  const canRetry = onRetry && state.actions.includes(`retry:${proposal.id}`);
```

(delete the old `retry` variable). Update the JSDoc `@param` lines accordingly.

- [ ] **Step 10: Syntax-check every module**

Run: `for %f in (static\js\*.js static\js\pages\*.js) do node --check %f`
Expected: no output (pages that import removed exports are fixed in Task 10; `node --check` only checks syntax).

- [ ] **Step 11: Commit**

```bash
git add -A static templates scripts/fetch_vendor.py .gitignore
git commit -m "Frontend shell: Xaman sign-in dialog, signed-in header, Xaman actions; drop GemWallet and the demo bar"
```

---

### Task 10: Pages — home, dashboard with faucet, agreement form, invite, lease

**Files:**
- Modify: `templates/escrow/home.html`, `templates/escrow/dashboard.html`, `templates/escrow/lease_create.html`, `templates/escrow/lease_detail.html`, `static/js/pages/dashboard.js`, `static/js/pages/create.js`, `static/js/pages/lease.js`, `static/js/pages/verify.js`, `templates/escrow/verify.html` (if it mentions the credential)
- Create: `templates/escrow/invite.html`, `static/js/pages/invite.js`, `static/js/pages/home.js`
- Delete: `templates/escrow/{demo,stage,arbiter}.html`, `static/js/pages/{demo,stage,arbiter}.js`

**Interfaces:**
- Consumes: Task 9 modules and Task 8 endpoints.

- [ ] **Step 1: Delete the demo pages**

```bash
git rm templates/escrow/demo.html templates/escrow/stage.html templates/escrow/arbiter.html static/js/pages/demo.js static/js/pages/stage.js static/js/pages/arbiter.js
```

- [ ] **Step 2: Home page**

In `templates/escrow/home.html` replace the `<div class="row">…</div>` holding the "Try the demo" link with:

```html
  <div class="row" data-home-cta>
    {% if not escrow_config.xaman_configured %}
    <p class="alert alert--warning">Sign-in is switched off: this server has no Xaman API key yet (see the README).</p>
    {% elif escrow_config.signed_in %}
    <a class="btn btn--primary btn--lg" href="{% url 'escrow:dashboard' %}">Go to your deposits <span class="btn__arrow" aria-hidden="true">&rarr;</span></a>
    {% else %}
    <button type="button" class="btn btn--primary btn--lg" data-home-signin>Sign in with Xaman <span class="btn__arrow" aria-hidden="true">&rarr;</span></button>
    {% endif %}
    <a class="link-more" href="#protocol">Read the protocol</a>
  </div>
  <p class="small muted">New here? Signing in with Xaman for the first time creates your account. Install Xaman on your phone and switch it to {{ escrow_config.network.name }}.</p>
```

Remove any other `{% url 'escrow:demo' %}` / `escrow:stage` / `escrow:arbiter` link from the page (search: `grep -n "escrow:demo\|escrow:stage\|escrow:arbiter\|demo" templates/escrow/home.html`), rewording demo copy ("Try the demo") to the real flow.

Add at the end of the template:

```html
{% block extra_js %}<script type="module" src="{% static 'js/pages/home.js' %}"></script>{% endblock %}
```

(and `{% load static %}` at the top if missing). Create `static/js/pages/home.js`:

```javascript
/** Home: the hero's sign-in button. */
import { startSignIn } from "../app.js";

document.querySelector("[data-home-signin]")?.addEventListener("click", (event) => startSignIn(event.currentTarget));
```

- [ ] **Step 3: Dashboard template**

Replace the content block of `templates/escrow/dashboard.html`:

```html
{% block content %}
<div class="page-header row row--between">
  <div>
    <h1>My deposits</h1>
    <p>Agreements where <span data-me-greeting>you</span> are the tenant, landlord or arbiter.</p>
  </div>
  <a class="btn btn--primary" href="{% url 'escrow:lease_create' %}">New agreement</a>
</div>

<section class="card" data-faucet aria-labelledby="faucet-heading">
  <div class="row row--between"><h2 id="faucet-heading" class="card__title">Test funds</h2><span class="badge">{{ escrow_config.network.name }} only</span></div>
  <p class="small muted">Test XRP and test tokens have no value. Each step works once a day.</p>
  <div class="mt-4" data-faucet-body><span class="skeleton"></span></div>
</section>

<div data-loading aria-hidden="true" class="mt-8"><span class="skeleton skeleton--block"></span></div>
<div class="stack mt-8" data-groups hidden></div>

<div class="card empty mt-8" data-empty hidden>
  <p>No agreements yet.</p>
  <a class="btn btn--primary" href="{% url 'escrow:lease_create' %}">Start your first agreement</a>
</div>

<div class="card empty mt-8" data-load-error hidden>
  <p data-load-error-text>The list couldn't be loaded.</p>
  <button type="button" class="btn btn--ghost" data-retry>Try again</button>
</div>
{% endblock %}
```

- [ ] **Step 4: `static/js/pages/dashboard.js`**

```javascript
/** My deposits: agreements grouped by stage, plus the Testnet faucet card. */
import { addTrustLine } from "../actions.js";
import { get, post } from "../api.js";
import { poll } from "../poll.js";
import { config, onMe } from "../session.js";
import { ROLE_LABEL, busy, el, fill, fmtTime, money, renderIfChanged, statusBadge, toast } from "../ui.js";

const $ = (selector) => document.querySelector(selector);
const GROUPS = [
  ["Waiting for acceptance", ["PENDING_ACCEPTANCE", "DRAFT", "SETTLEMENT_READY"]],
  ["Active", ["ACTIVE"]],
  ["In settlement", ["IN_SETTLEMENT"]],
  ["Closed", ["RETURNED", "SETTLED", "FAILED", "CANCELLED"]],
];
let poller = null;

function show(name) {
  for (const key of ["loading", "groups", "empty", "load-error"]) $(`[data-${key}]`).hidden = key !== name;
}

function row(lease) {
  const others = lease.parties.filter((p) => p.role !== lease.role).map((p) => `${p.name} (${p.role})`);
  if (lease.open_roles.length) others.push(`waiting for ${lease.open_roles.join(" and ")}`);
  return el("tr", {},
    el("td", {}, el("a", { href: `/leases/${lease.id}/`, text: lease.title || `Deposit ${lease.id.slice(0, 8)}` }),
      el("div", { class: "small muted", text: others.join(" · ") })),
    el("td", { text: ROLE_LABEL[lease.role] || lease.role }),
    el("td", {}, el("time", { dateTime: lease.cancel_after, text: fmtTime(lease.cancel_after) })),
    el("td", { class: "num", text: money(lease.amount, lease.currency) }),
    el("td", {}, statusBadge(lease.status)));
}

function table(title, leases) {
  return el("section", {},
    el("h2", { class: "h3", text: `${title} (${leases.length})` }),
    el("div", { class: "table-wrap mt-4" }, el("table", { class: "table" },
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: "Property" }), el("th", { scope: "col", text: "Your role" }),
        el("th", { scope: "col", text: "Automatic return" }), el("th", { scope: "col", class: "num", text: "Deposit" }),
        el("th", { scope: "col", text: "Status" }))),
      el("tbody", {}, leases.map(row)))));
}

async function refresh() {
  try {
    const { leases } = await get("/api/leases/", { quiet: true });
    if (leases.length === 0) return show("empty");
    renderIfChanged($("[data-groups]"), leases, (node) => {
      node.replaceChildren(...GROUPS.map(([title, statuses]) => [title, leases.filter((l) => statuses.includes(l.status))])
        .filter(([, list]) => list.length).map(([title, list]) => table(title, list)));
    });
    return show("groups");
  } catch (error) {
    $("[data-load-error-text]").textContent = error.message;
    return show("load-error");
  }
}

/* ---------- faucet ---------- */
async function faucetStep(button, label, fn, success) {
  await busy(button, label, async () => {
    try {
      await fn();
      toast(success, "success");
    } catch (error) {
      if (!error.code) toast(error.message, "warning");
    }
    await renderFaucet();
  });
}

async function renderFaucet() {
  const body = $("[data-faucet-body]");
  let data;
  try {
    data = await get("/api/faucet/", { quiet: true });
  } catch (error) {
    fill(body, el("p", { class: "alert alert--warning", text: error.message }));
    return;
  }
  const steps = [];
  if (!data.activated) {
    steps.push(el("p", { text: "Your account doesn't exist on the ledger yet. Activate it with 50 test XRP." }),
      el("button", { type: "button", class: "btn btn--primary", disabled: !data.available.activate,
        on: { click: (e) => faucetStep(e.currentTarget, "Sending test XRP…", () => post("/api/faucet/activate/"), "Account activated with 50 test XRP.") } },
      data.available.activate ? "Activate account" : "Activated in the last 24 hours"));
  } else {
    steps.push(el("p", {}, "Balances: ", Object.entries(data.balances).map(([code, value]) => money(value, code)).join(" · ") || "none"));
    for (const currency of config.currencies) {
      const hasLine = data.trust_lines[currency.code];
      const canGet = data.available[`tokens:${currency.code}`];
      steps.push(el("div", { class: "actions" },
        hasLine
          ? el("span", { class: "badge badge--success", text: `${currency.code} trust line set` })
          : el("button", { type: "button", class: "btn", on: { click: (e) => faucetStep(e.currentTarget, "Confirm in Xaman…", () => addTrustLine(currency.code), `Your account can now hold ${currency.label}.`) } }, `Add ${currency.code} trust line`),
        hasLine && el("button", { type: "button", class: "btn btn--primary", disabled: !canGet,
          on: { click: (e) => faucetStep(e.currentTarget, "Sending test tokens…", () => post("/api/faucet/tokens/", { currency: currency.code }), `5,000 ${currency.label} sent.`) } },
        canGet ? `Get 5,000 ${currency.label}` : `${currency.code} received in the last 24 hours`)));
    }
  }
  fill(body, ...steps);
}

onMe((me) => { if (me) $("[data-me-greeting]").textContent = me.name; });
$("[data-retry]").addEventListener("click", () => { show("loading"); poller.now(); });
poller = poll(refresh, 5000);
renderFaucet();
```

If `ui.fill` doesn't accept multiple children, check its signature in `static/js/ui.js` (`fill(node, ...children)` per its use in `lease.js`: `fill(node, el(...), form && el(...))`), which it does.

- [ ] **Step 5: Agreement form template**

Replace the content block of `templates/escrow/lease_create.html`:

```html
{% block content %}
<div class="page-header">
  <h1>New deposit agreement</h1>
  <p>Fill in the terms. Nothing goes on the ledger until the other side and the arbiter have accepted through their invite links.</p>
</div>

<form class="form card" data-lease-form novalidate>
  <fieldset class="form">
    <legend>You are</legend>
    <div class="segmented" role="radiogroup" aria-label="Your role">
      <label><input type="radio" name="role" value="tenant" checked><span>The tenant</span></label>
      <label><input type="radio" name="role" value="landlord"><span>The landlord</span></label>
    </div>
  </fieldset>

  <fieldset class="form">
    <legend>Lease</legend>
    <div class="field">
      <label for="id_title">Property name</label>
      <input id="id_title" name="title" type="text" maxlength="120" autocomplete="off" aria-describedby="title-help" placeholder="Via Sparano 12, flat 3">
      <small id="title-help">Optional. Stored privately by safeDeposit and never written to the ledger.</small>
    </div>
    <div class="grid grid--2">
      <div class="field">
        <label for="id_amount">Deposit</label>
        <input id="id_amount" name="amount" type="text" inputmode="decimal" autocomplete="off" required data-amount value="1000">
      </div>
      <div class="field">
        <label for="id_currency">Currency</label>
        <select id="id_currency" name="currency" required>
          {% for currency in escrow_config.currencies %}<option value="{{ currency.code }}">{{ currency.label }}</option>{% endfor %}
        </select>
      </div>
    </div>
    <div class="field">
      <span class="label" id="end-label">Lease end</span>
      <div class="segmented" role="radiogroup" aria-labelledby="end-label">
        <label><input type="radio" name="end_mode" value="date" checked><span>On a date</span></label>
        <label><input type="radio" name="end_mode" value="minutes"><span>In some minutes (for testing)</span></label>
      </div>
    </div>
    <div class="grid grid--2">
      <div class="field" data-end-date>
        <label for="id_end_date">Ends on</label>
        <input id="id_end_date" name="end_date" type="date" required>
      </div>
      <div class="field" data-end-minutes hidden>
        <label for="id_end_in_minutes">Ends in (minutes)</label>
        <input id="id_end_in_minutes" name="end_in_minutes" type="number" min="15" max="525600" step="1" value="30">
      </div>
      <div class="field">
        <label for="id_grace_minutes">Grace period (minutes)</label>
        <input id="id_grace_minutes" name="grace_minutes" type="number" min="0" max="525600" step="1" value="1440" required aria-describedby="grace-help">
        <small id="grace-help">Time after the lease end in which a damage claim is still possible. Then the deposit returns automatically.</small>
      </div>
    </div>
  </fieldset>

  <fieldset class="form">
    <legend>Arbiter</legend>
    <div class="field">
      <label for="id_arbiter_address">Arbiter's XRP Ledger address</label>
      <input id="id_arbiter_address" class="mono" name="arbiter_address" type="text" maxlength="64" autocomplete="off" spellcheck="false" placeholder="r…" aria-describedby="arbiter-help">
      <small id="arbiter-help">Optional. Someone independent of both of you. Leave it empty to send an arbiter invite link instead. The other side must approve the arbiter too.</small>
    </div>
  </fieldset>

  <label class="checkbox">
    <input type="checkbox" name="accept_terms" required>
    <span class="small">I agree to the <a href="{% url 'legal:terms' %}">Terms</a> and have read the <a href="{% url 'legal:privacy' %}">Privacy notice</a>. I understand that ledger transactions can't be reversed.</span>
  </label>

  <div class="row">
    <button type="submit" class="btn btn--primary" data-submit>Create agreement and invite links</button>
    <a class="btn btn--ghost" href="{% url 'escrow:dashboard' %}">Cancel</a>
  </div>
</form>
{% endblock %}
```

- [ ] **Step 6: `static/js/pages/create.js`**

```javascript
/** New agreement: send the terms, then go to the lease page, which shows the invite links. */
import { post } from "../api.js";
import { busy, toast } from "../ui.js";

const form = document.querySelector("[data-lease-form]");
const endDate = form.elements.end_date;
endDate.min = new Date(Date.now() + 86400000).toISOString().slice(0, 10);

form.querySelectorAll("input[name=end_mode]").forEach((radio) => radio.addEventListener("change", () => {
  const byDate = form.elements.end_mode.value === "date";
  form.querySelector("[data-end-date]").hidden = !byDate;
  form.querySelector("[data-end-minutes]").hidden = byDate;
  endDate.required = byDate;
}));

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!form.elements.accept_terms.checked) return toast("Please accept the terms first.", "warning");
  const byDate = form.elements.end_mode.value === "date";
  if (byDate && !endDate.value) return toast("Choose the lease end date.", "warning");
  const body = {
    role: form.elements.role.value,
    title: form.elements.title.value.trim(),
    amount: form.elements.amount.value.trim(),
    currency: form.elements.currency.value,
    grace_minutes: form.elements.grace_minutes.value,
    arbiter_address: form.elements.arbiter_address.value.trim(),
    ...(byDate ? { end_date: endDate.value } : { end_in_minutes: form.elements.end_in_minutes.value }),
  };
  await busy(form.querySelector("[data-submit]"), "Creating…", async () => {
    try {
      const { lease_id: leaseId } = await post("/api/leases/", body);
      window.location.assign(`/leases/${leaseId}/`);
    } catch { /* shown by api.js */ }
  });
  return undefined;
});
```

- [ ] **Step 7: Invite page**

`templates/escrow/invite.html`:

```html
{% extends "base.html" %}
{% load static %}

{% block title %}Invitation{% endblock %}

{% block content %}
<div data-invite-page data-token="{{ token }}">
  <div class="page-header">
    <h1 data-title>Invitation</h1>
    <p data-subtitle class="muted"></p>
  </div>
  <div class="card" data-terms><span class="skeleton skeleton--block"></span></div>
  <div class="card mt-8" data-decision hidden></div>
</div>
{% endblock %}

{% block extra_js %}<script type="module" src="{% static 'js/pages/invite.js' %}"></script>{% endblock %}
```

`static/js/pages/invite.js`:

```javascript
/** Invite link: show the terms, ask for sign-in, then accept or decline. */
import { get, post } from "../api.js";
import { startSignIn } from "../app.js";
import { config } from "../session.js";
import { ROLE_LABEL, busy, el, fill, fmtTime, money } from "../ui.js";

const root = document.querySelector("[data-invite-page]");
const token = root.dataset.token;
const $ = (selector) => root.querySelector(selector);
const RULES = {
  landlord: "As landlord you receive any agreed damage payout. You can claim damage until the grace period ends; after that the deposit returns to the tenant automatically.",
  tenant: "As tenant you lock the deposit from your own Xaman account. If nothing goes wrong it comes back to you automatically after the lease end plus the grace period.",
  arbiter: "As arbiter you only act in a dispute: any two of tenant, landlord and you must approve how the deposit is split. You never hold the money.",
};

async function render() {
  const data = await get(`/api/invites/${token}/`);
  const lease = data.lease;
  $("[data-title]").textContent = `You're invited as ${ROLE_LABEL[data.role].toLowerCase()}`;
  $("[data-subtitle]").textContent = `${lease.created_by} started this agreement${lease.title ? ` for ${lease.title}` : ""}.`;
  fill($("[data-terms]"),
    el("h2", { class: "card__title", text: "Terms" }),
    el("dl", { class: "details" },
      el("dt", { text: "Deposit" }), el("dd", { text: money(lease.amount, lease.currency_label) }),
      el("dt", { text: "Lease ends" }), el("dd", { text: fmtTime(lease.end_at) }),
      el("dt", { text: "Automatic return" }), el("dd", { text: `${fmtTime(lease.cancel_after)} (after a grace period of ${lease.grace_minutes} minutes)` }),
      el("dt", { text: "Parties so far" }), el("dd", { text: lease.parties.map((p) => `${p.name} (${p.role}, ${p.address})`).join("; ") || "none" }),
      el("dt", { text: "Network" }), el("dd", { text: `XRP Ledger ${config.network.name}: test funds only` })),
    el("p", { class: "mt-4", text: RULES[data.role] }));

  const decision = $("[data-decision]");
  decision.hidden = false;
  if (lease.status !== "PENDING_ACCEPTANCE") {
    return fill(decision, el("p", { text: `This agreement is ${lease.status_label.toLowerCase()}.` }),
      data.accepted_by_you && el("a", { class: "btn btn--primary", href: `/leases/${lease.id}/`, text: "Open the agreement" }));
  }
  if (data.accepted_by_you) return fill(decision, el("p", { text: "You've accepted." }), el("a", { class: "btn btn--primary", href: `/leases/${lease.id}/`, text: "Open the agreement" }));
  if (data.used) return fill(decision, el("p", { class: "alert alert--warning", text: "This invite has already been used." }));
  if (!data.signed_in) {
    return fill(decision, el("p", { text: "Sign in with Xaman to accept. Signing in for the first time creates your account." }),
      el("button", { type: "button", class: "btn btn--primary", text: "Sign in with Xaman", on: { click: async (e) => { await startSignIn(e.currentTarget); await render(); } } }));
  }
  if (data.already_party) return fill(decision, el("p", { class: "alert alert--warning", text: "You already have another role in this agreement." }));
  return fill(decision,
    el("div", { class: "actions" },
      el("button", { type: "button", class: "btn btn--primary", text: data.role === "arbiter" ? "Accept the arbiter role" : "Accept", on: { click: (e) => decide(e.currentTarget, "accept") } }),
      el("button", { type: "button", class: "btn btn--ghost", text: "Decline", on: { click: (e) => decide(e.currentTarget, "decline") } })));
}

async function decide(button, what) {
  if (what === "decline" && !window.confirm("Decline? This closes the agreement for everyone.")) return;
  await busy(button, what === "accept" ? "Accepting…" : "Declining…", async () => {
    try {
      const result = await post(`/api/invites/${token}/${what}/`);
      if (result.lease_id) window.location.assign(`/leases/${result.lease_id}/`);
      else await render();
    } catch { /* shown by api.js */ }
  });
}

render().catch((error) => fill($("[data-terms]"), el("p", { class: "alert alert--warning", text: error.message })));
```

(`startSignIn` only redirects when there is a `next` parameter or the page is `/`; on the invite page it returns, and `render()` then shows the Accept buttons.)

- [ ] **Step 8: Lease page**

In `templates/escrow/lease_detail.html` add, right after `<div data-outcome></div>`:

```html
    <section class="card mt-8" data-agreement hidden aria-labelledby="agreement-heading">
      <h2 id="agreement-heading" class="card__title">Waiting for acceptance</h2>
      <div data-agreement-body></div>
    </section>
```

and change `data-forbidden-text`'s default text to `This deposit isn't visible to your account.`

In `static/js/pages/lease.js`:

8a. Imports:

```javascript
import { anchorEvidence, approveSplit, addTrustLine, lockDeposit, proposeSplit, releaseDeposit, retryPayout, uploadEvidence, verifyEvidence } from "../actions.js";
import { get, post } from "../api.js";
import { partyCard, proposalCard, setupChecklist, splitBar, syncEvents } from "../parts.js";
import { poll } from "../poll.js";
import { config } from "../session.js";
import { splitForm } from "../splitform.js";
import { ROLE_LABEL, busy, copyButton, el, extLink, fill, fmtCountdown, fmtTime, money, renderIfChanged, shortAddress, shortHash, statusBadge, statusLine, toast } from "../ui.js";
```

8b. Header comment: `Lease detail. Polls GET /api/leases/<id>/ every 3 s and renders it. What the account may do comes ONLY from the API's actions list.`

8c. In `refresh`'s 404 text: `"This deposit isn't visible to your account."`.

8d. `act`: wallet / Xaman errors have no `.code`; keep as is. Change the `SUBMITTING` constant usage: Xaman steps use `"Confirm in Xaman…"`. Add `const IN_XAMAN = "Confirm in Xaman…";`.

8e. In `render()`: guard the ledger panels for pending agreements and render the agreement card. Replace the body from `renderIfChanged($("[data-outcome]")…` through `tick();` with:

```javascript
  renderIfChanged($("[data-outcome]"), [s.status, s.error, s.proposals.find((p) => p.status === "EXECUTED")], (node) => renderOutcome(node, s, tenant));
  renderIfChanged($("[data-parties]"), [s.parties, s.open_roles, me?.party_id], (node) => node.replaceChildren(
    ...s.parties.map((p) => partyCard(p, me)),
    ...s.open_roles.map((role) => el("article", { class: `card party role-${role}` }, el("p", { class: "sc muted", text: ROLE_LABEL[role] }), el("p", { class: "muted", text: "Not accepted yet" })))));
  renderAgreement(s);
  const onLedger = !["PENDING_ACCEPTANCE", "CANCELLED"].includes(s.status) || Boolean(s.settlement);
  $("[data-ledger-cards]").hidden = !onLedger;
  if (onLedger) {
    renderSetup(s, me);
    renderEscrow(s);
    renderIfChanged($("[data-settlement]"), s.settlement, (node) => renderSettlement(node, s));
    $("[data-live-tag]").hidden = !(s.settlement && s.settlement.source === "ledger");
  } else {
    $("[data-setup]").hidden = true;
  }
  renderActions(s, me);
  renderProposals(s);
  renderEvidence(s, me);
  syncEvents($("[data-events]"), s.events);
  $("[data-no-events]").hidden = s.events.length > 0;
  tick();
```

`tenant` can now be missing: change `const tenant = s.parties[0];` to `const tenant = s.parties.find((p) => p.role === "tenant") || { name: "the tenant" };`.

In `renderOutcome` add a CANCELLED branch before the final `else`:

```javascript
  } else if (s.status === "CANCELLED") {
    node.replaceChildren(el("div", { class: "outcome outcome--failed" },
      el("p", { class: "outcome__mark", "aria-hidden": "true", text: "×" }),
      el("h2", { text: "This agreement was cancelled" }),
      el("p", { text: s.error || "It was closed before any deposit was locked." })));
```

and in the FAILED branch, keep "Start again" linking to `/leases/new/`.

8f. Add `renderAgreement`:

```javascript
function renderAgreement(s) {
  const card = $("[data-agreement]");
  card.hidden = s.status !== "PENDING_ACCEPTANCE";
  if (card.hidden || acting) return;
  renderIfChanged($("[data-agreement-body]"), [s.agreement, s.invites, s.actions, s.parties, openForm], (node) => {
    const a = s.agreement;
    const arbiter = s.parties.find((p) => p.role === "arbiter");
    const check = (done, text) => el("li", { class: done ? "is-done" : "" }, el("span", { text }));
    const items = el("ul", { class: "checklist" },
      check(s.parties.some((p) => p.role === "tenant"), "Tenant accepted"),
      check(s.parties.some((p) => p.role === "landlord"), "Landlord accepted"),
      check(a.arbiter_accepted, arbiter ? `Arbiter ${arbiter.name} accepted the role` : "Arbiter accepted the role"),
      check(a.tenant_approved_arbiter, "Tenant approved the arbiter"),
      check(a.landlord_approved_arbiter, "Landlord approved the arbiter"));
    const invites = s.invites.map((invite) => el("div", { class: "field" },
      el("span", { class: "label", text: `Invite link for the ${invite.role}` }),
      el("div", { class: "row" }, el("code", { class: "mono small", text: invite.url }), copyButton(invite.url, "Copy link"))));
    const buttons = [];
    if (s.actions.includes("approve_arbiter")) {
      buttons.push(el("button", { type: "button", class: "btn btn--primary", text: `Approve ${arbiter ? arbiter.name : "the arbiter"}`,
        on: { click: (e) => act(e.currentTarget, "Saving…", () => post(`/api/leases/${leaseId}/approve-arbiter/`)) } }));
    }
    if (s.actions.includes("change_arbiter")) {
      buttons.push(el("button", { type: "button", class: "btn", "aria-expanded": String(openForm === "arbiter"), on: { click: () => toggleForm("arbiter") } }, arbiter ? "Propose a different arbiter" : "Name the arbiter by address"));
    }
    if (s.actions.includes("cancel")) {
      buttons.push(el("button", { type: "button", class: "btn btn--ghost", text: "Cancel agreement",
        on: { click: (e) => { if (window.confirm("Cancel this agreement for everyone?")) act(e.currentTarget, "Cancelling…", () => post(`/api/leases/${leaseId}/cancel/`)); } } }));
    }
    let form = null;
    if (openForm === "arbiter" && s.actions.includes("change_arbiter")) {
      const input = el("input", { class: "mono", name: "address", maxlength: "64", placeholder: "r…", spellcheck: "false", autocomplete: "off", "aria-label": "Arbiter's XRP Ledger address" });
      form = el("form", { class: "form mt-4", on: { submit: (e) => {
        e.preventDefault();
        act(e.submitter || e.currentTarget.querySelector("button"), "Saving…", async () => {
          const next = await post(`/api/leases/${leaseId}/arbiter/`, { address: input.value.trim() });
          openForm = null;
          return next;
        });
      } } }, el("p", { class: "small muted", text: "Changing the arbiter resets both approvals and the arbiter's acceptance, and creates a new arbiter invite link." }), input,
        el("div", { class: "actions" }, el("button", { type: "submit", class: "btn btn--primary", text: "Propose this arbiter" })));
    }
    fill(node, items, invites.length > 0 && el("div", { class: "mt-4 stack" }, el("p", { class: "small muted", text: "Send each link to the right person. Each works once." }), ...invites),
      buttons.length > 0 && el("div", { class: "actions mt-4" }, buttons), form);
  });
}
```

8g. `lockStep(s, me)` becomes (wallet checks use the faucet endpoint):

```javascript
function lockStep(s) {
  if (s.status === "DRAFT") return statusLine("Waiting for the ledger to validate each step (about 20 s in total)…");
  if (!s.actions.includes("lock_deposit")) {
    const tenant = s.parties.find((p) => p.role === "tenant");
    return el("p", { class: "note mt-4", text: `The settlement account is ready. Waiting for ${tenant ? tenant.name : "the tenant"} to lock the deposit.` });
  }
  const box = el("div", { class: "mt-8 stack" },
    el("p", { text: `Lock ${money(s.amount, s.currency)} in an escrow that pays into the settlement account, or back to you after ${fmtTime(s.cancel_after)}.` }));
  const funds = faucetState;
  if (funds && !funds.trust_lines[s.currency]) {
    box.append(el("p", { class: "alert alert--warning", text: `Your account can't hold ${s.currency_label} yet.` }),
      el("button", { type: "button", class: "btn", text: `Add ${s.currency} trust line in Xaman`, on: { click: (event) => walletStep(event.currentTarget, () => addTrustLine(s.currency)) } }));
    return box;
  }
  if (funds && Number(funds.balances[s.currency] || 0) < Number(s.amount)) {
    box.append(el("p", { class: "alert alert--warning" }, `You hold ${money(funds.balances[s.currency] || 0, s.currency)}; the deposit is ${money(s.amount, s.currency)}. Get test tokens on `, el("a", { href: "/leases/", text: "your dashboard" }), "."));
    return box;
  }
  box.append(el("div", { class: "actions" }, el("button", {
    type: "button", class: "btn btn--primary btn--lg",
    on: { click: (event) => act(event.currentTarget, IN_XAMAN, () => lockDeposit(leaseId)) },
  }, "Lock deposit in Xaman")), el("p", { class: "small muted", text: "Signed in your own Xaman app. safeDeposit never sees your key." }));
  return box;
}

let faucetState = null;
async function loadFaucet() {
  try { faucetState = await get("/api/faucet/", { quiet: true }); } catch { faucetState = null; }
}

async function walletStep(button, fn) {
  await busy(button, IN_XAMAN, async () => {
    try {
      await fn();
      await loadFaucet();
      render();
    } catch (error) {
      if (!error.code) toast(error.message, "warning");
    }
  });
}
```

In `renderSetup`, call `lockStep(s)` and add `faucetState` to the `renderIfChanged` keys (replace `currentMe()?.balances` with `faucetState`). At the bottom, before `poller = poll(refresh, 3000);`, add `loadFaucet().then(() => state && render());`.

8h. In `renderActions`: remove `me.can_sign` uses. Release buttons call `act(event.currentTarget, "Submitting to the XRPL (about 4 s)", () => releaseDeposit(leaseId, "early_move_out"))`. The damage claim `onSubmit`:

```javascript
        onSubmit: (values, button) => act(button, "Releasing and preparing the split…", async () => {
          await releaseDeposit(leaseId, "damage_claim");
          openForm = null;
          const { proposal_id: proposalId } = await proposeSplit(leaseId, values.tenant, values.landlord, values.reason);
          return approveSplit(proposalId);
        }),
```

The propose form:

```javascript
        intro: `The settlement account holds ${money(s.settlement.balance, s.currency)}. A new proposal replaces the open one; you then approve it in Xaman.`,
        onCancel: () => toggleForm(null),
        onSubmit: (values, button) => act(button, "Preparing the payout…", async () => {
          const { proposal_id: proposalId } = await proposeSplit(leaseId, values.tenant, values.landlord, values.reason);
          openForm = null;
          return approveSplit(proposalId);
        }),
```

`relevant` filter also excludes the pending actions: `const relevant = s.actions.filter((a) => !a.startsWith("sign:") && !a.startsWith("retry:") && !["upload_evidence", "lock_deposit", "approve_arbiter", "change_arbiter", "cancel"].includes(a));`. Delete the `confirmLabel` line.

8i. `renderProposals`: `proposalCard(proposal, s, (button) => act(button, IN_XAMAN, () => approveSplit(proposal.id)), (button) => act(button, "Submitting…", () => retryPayout(proposal.id)))`.

8j. Evidence: the anchor button uses `act(event.currentTarget, IN_XAMAN, async () => { await anchorEvidence(item); })`. The upload handler label becomes `"Uploading, then confirm in Xaman…"` and calls `uploadEvidence(leaseId, data)`. The "Verify an altered copy" button title becomes `"Changes one byte of the photo in memory, then checks it"`.

8k. `tick()`: replace the `fill(label, …)` with `label.textContent = "Return date passed. The bot returns it about 10 s later.";` and drop the `config.demo_mode` button. Delete the `onPersona` and `bot:ran` listeners at the bottom.

8l. `config` is still imported for nothing else? If unused, remove it from the import.

- [ ] **Step 9: Verify page**

In `static/js/pages/verify.js` delete the credential check (the block that reads `credential_registry` / `arbiter_credential` and its `check(...)` around line 95) and any import it alone used. In `templates/escrow/verify.html` remove any credential wording (`grep -n -i "credential" templates/escrow/verify.html`).

- [ ] **Step 10: Syntax-check and run the tests**

Run: `for %f in (static\js\*.js static\js\pages\*.js) do node --check %f`
Expected: no output.
Run: `grep -rn "demo_mode\|wallet.js\|onPersona\|can_sign\|verified_arbiter\|runBot\|/api/demo\|escrow:demo\|escrow:arbiter\|escrow:stage" static templates core`
Expected: no matches.
Run: `.venv\Scripts\python manage.py test`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add -A static templates
git commit -m "Pages for real accounts: home sign-in, dashboard with faucet, agreement form, invites, Xaman lease actions"
```

---

### Task 11: Commands, setup script and docs

**Files:**
- Modify: `core/management/commands/preflight.py`, `core/management/commands/run_bot.py`, `scripts/setup.py`, `README.md`, `CLAUDE.md`
- Delete: `scripts/api_walkthrough.py`, `scripts/wallet_walkthrough.py`

- [ ] **Step 1: Preflight with throw-away wallets**

In `core/management/commands/preflight.py`:
- Docstring: `"""Prove the network is usable: server reachable, issuers configured, token escrow works (throw-away wallets)."""`
- Every "bootstrap_demo" in messages becomes "bootstrap"; `DISABLED_HINT` likewise.
- Replace the persona lookup (`tenant = Party.objects.filter(...)`, `landlord = ...`, the "Demo personas … missing" check and the `escrow_round_trip(client, Wallet.from_seed(tenant.seed), landlord.address, eur)` call) with:

```python
        eur = Currency.objects.filter(code="EUR").first()
        if eur is None:
            self.fail("The EUR currency is missing. Run bootstrap first.")
        treasury = Wallet.from_seed(PlatformWallet.objects.get(role="treasury").seed)
        issuer = Wallet.from_seed(PlatformWallet.objects.get(role="issuer_eur").seed)
        tenant, landlord = Wallet.create(), Wallet.create()
        for wallet in (tenant, landlord):
            services.fund_xrp(treasury, wallet.address, Decimal("15"))
        services.ensure_trust_line(tenant, "EUR", eur.issuer_address)
        services.ensure_trust_line(landlord, "EUR", eur.issuer_address)
        services.issue_tokens(issuer, tenant.address, "EUR", Decimal("5"))
        self.ok("Throw-away tenant and landlord funded (15 XRP, 5 EUR)")
        self.escrow_round_trip(client, tenant, landlord.address, eur)
```

Use the module's existing pass / fail helper names (read the file: if they are named differently than `self.ok` / `self.fail`, use those). Add imports `from decimal import Decimal` and `PlatformWallet` (drop `Party`). If the treasury lacks XRP, call `services.faucet_top_up(treasury)` first when `services.get_balances(treasury.address)["XRP"] < Decimal("40")`.

- [ ] **Step 2: Bot command help**

In `run_bot.py` change the help to `"Loop bot_pass() every 10 seconds (Ctrl+C to stop): returns deposits after their return date and closes expired agreements."`.

- [ ] **Step 3: Setup script**

In `scripts/setup.py`:
- `--demo` becomes `--bootstrap`: help `"Also create the platform wallets on the XRPL test network and run preflight (needs internet, about 2 minutes)."`; runs `manage.py bootstrap` then `manage.py preflight`.
- After `make_env_file()`, warn when the Xaman keys are empty:

```python
    env = (ROOT / ".env").read_text(encoding="utf-8")
    if "XAMAN_API_KEY=\n" in env or "XAMAN_API_KEY=" not in env:
        print("NOTE: set XAMAN_API_KEY and XAMAN_API_SECRET in .env (free at https://apps.xumm.dev), or nobody can sign in.")
```

- The final "Next" lines: `manage.py bootstrap` (when not run), `runserver`, `run_bot`; delete the `demo_scenarios` line.

- [ ] **Step 4: Remove the walkthrough scripts**

```bash
git rm scripts/api_walkthrough.py scripts/wallet_walkthrough.py
```

- [ ] **Step 5: README.md**

Rewrite these sections (keep "How it works", the mermaid diagram, "Branding rules"):
- Intro: drop "demo-ready, not production-ready" → "Runs on the XRPL Testnet with real Xaman accounts; not for mainnet."
- **XRPL features used**: drop the Credentials row.
- **Accounts and signing**: sign in with Xaman (QR / deep link / push); every action is a Xaman sign request; settlement approvals are Xaman multisignatures; releases are submitted by the platform bot so the release secret stays on the server.
- **Agreements**: draft → invite links → all accept, both sides approve the arbiter → settlement account → tenant locks.
- **Setup**: `py scripts\setup.py --bootstrap`; register an app at https://apps.xumm.dev, put the key and secret in `.env`; in the Xaman app switch to Testnet (Settings → Advanced → Node). Run `runserver` and `run_bot`.
- **Test funds**: the dashboard card (activate, trust line, 5,000 test tokens, once a day).
- **Checks**: `manage.py test`, `manage.py smoke_test --scenario all`, `manage.py preflight`, `scripts/xaman_multisign_spike.py`.
- Delete: "The live demo", "Manual test script" (replace with a short manual test: two browsers / two Xaman accounts plus a third for the arbiter, walk A/B/C), "Browser wallets (GemWallet)".
- **Known limitations**: no email notifications (invite links are shared by hand); Xaman needed on a phone; the platform's coordinating backend builds transactions and stores the release secret; Testnet only.

- [ ] **Step 6: CLAUDE.md**

Update: decisions list (Xaman only, no demo mode, real accounts), "Stack" (Xaman REST via `ledger/xaman.py`, `segno`), "Data model" (Party as account, Invite, XamanRequest, FaucetGrant, new statuses), "API contract" (replace with the Task 8 table), the security rules ("Demo seeds may be stored" → "Only platform wallet seeds are stored"), and add a Progress entry: `- Real accounts with Xaman (branch real-accounts-testnet): done <date>. Spec and plan in docs/superpowers/.`

- [ ] **Step 7: Final verification**

Run: `.venv\Scripts\python manage.py test`
Expected: PASS, 0 failures.
Run: `.venv\Scripts\python manage.py check`
Expected: `System check identified no issues (0 silenced).`
Run (needs internet): `.venv\Scripts\python manage.py bootstrap` then `.venv\Scripts\python manage.py preflight`
Expected: `Preflight passed`.

Manual run (needs Xaman keys): start `runserver` and `run_bot`; on http://127.0.0.1:8000 sign in with Xaman account A, set a name, activate + trust line + tokens on the dashboard, create an agreement as tenant, open the landlord link in a private window and sign in with account B, accept; open the arbiter link with account C, accept; approve the arbiter as A and B; lock as A; release early as A; approve the split as B. Expected: status Settled with payout links.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "Preflight with throw-away wallets, setup for Xaman keys, docs for real accounts"
```
