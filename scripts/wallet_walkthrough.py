#!/usr/bin/env python
"""Prove the browser-wallet paths end to end on the test network, without a browser extension.

    python manage.py runserver                 # terminal 1
    python scripts/wallet_walkthrough.py       # terminal 2   [--base URL]

A throw-away wallet is created here and plays the tenant's own wallet: it signs the sign-in
challenge, the EscrowCreate, the EscrowFinish and an evidence anchor locally (as GemWallet would),
and the server only ever sees public data and transaction hashes. The key never leaves this process
and is not saved anywhere.
"""
import argparse
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from xrpl.core import keypairs  # noqa: E402
from xrpl.models.transactions.transaction import Transaction  # noqa: E402
from xrpl.transaction import sign  # noqa: E402
from xrpl.wallet import Wallet  # noqa: E402

from core import lifecycle  # noqa: E402
from core.models import Currency  # noqa: E402
from ledger import services  # noqa: E402
from ledger.client import get_client  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + os.urandom(64)  # passes the server's file-signature check; unique per run


class Api:
    def __init__(self, base):
        self.base, self.http = base.rstrip("/"), requests.Session()
        self.get("/api/me/")

    def _result(self, response):
        data = response.json()
        if response.status_code >= 400:
            raise SystemExit(f"FAIL {response.request.method} {response.url} -> {response.status_code} {data.get('code')}: {data.get('error')}")
        return data

    def get(self, path):
        return self._result(self.http.get(self.base + path, timeout=120))

    def post(self, path, body=None, files=None, data=None, ok=(200, 201)):
        headers = {"X-CSRFToken": self.http.cookies.get("csrftoken", ""), "Referer": self.base + "/"}
        if files:
            response = self.http.post(self.base + path, files=files, data=data, headers=headers, timeout=180)
        else:
            response = self.http.post(self.base + path, json=body or {}, headers=headers, timeout=180)
        return self._result(response)


def wallet_submit(wallet, tx_json):
    """What the wallet extension does: sign the server's unsigned transaction and submit it. -> hash"""
    tx_json = {k: v for k, v in tx_json.items() if k not in ("SigningPubKey", "TxnSignature")}
    signed = sign(Transaction.from_xrpl(tx_json), wallet)
    return services._send_signed(signed, get_client())["hash"]


def step(text):
    print(f"  ok  {text}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    wallet = Wallet.create()
    eur = Currency.objects.get(code="EUR")
    services.fund_xrp(lifecycle.platform_wallet("treasury"), wallet.address, 12)
    step(f"throw-away wallet funded: {wallet.address}")

    api = Api(args.base)
    personas = {p["role"]: p for p in api.get("/api/demo/personas/")["personas"]}

    challenge = api.get(f"/api/wallet/challenge/?address={wallet.address}")
    signature = keypairs.sign(challenge["message"].encode(), wallet.private_key)
    me = api.post("/api/wallet/link/", {"address": wallet.address, "role": "tenant", "public_key": wallet.public_key, "signature": signature})
    assert me["wallet_type"] == "gemwallet" and not me["can_sign"], me
    step("signed challenge accepted; session acts as the wallet (no key on the server)")

    wallet_submit(wallet, services.autofill_trust_set(wallet.address, "EUR", eur.issuer_address))
    time.sleep(1)
    api.post("/api/demo/fund/")
    assert api.get("/api/me/")["balances"].get("EUR"), "no EUR after top-up"
    step("wallet added its trust line; issuer topped it up")

    lease_id = api.post("/api/leases/", {
        "landlord_id": personas["landlord"]["party_id"], "arbiter_id": personas["arbiter"]["party_id"],
        "currency": "EUR", "amount": "250", "end_in_minutes": 30, "grace_minutes": 5, "title": "Wallet walkthrough",
    })["lease_id"]
    for _ in range(90):
        state = api.get(f"/api/leases/{lease_id}/")
        if state["status"] != "DRAFT":
            break
        time.sleep(2)
    assert state["status"] == "SETTLEMENT_READY", state["status"] + " " + state["error"]
    step("settlement account ready")

    tx_hash = wallet_submit(wallet, api.get(f"/api/leases/{lease_id}/escrow-tx/")["tx"])
    state = api.post(f"/api/leases/{lease_id}/escrow/", {"mode": "wallet", "tx_hash": tx_hash})
    assert state["status"] == "ACTIVE", state["status"]
    step(f"wallet-signed EscrowCreate verified by the server: {state['events'][0]['explorer_url']}")

    item = api.post(f"/api/leases/{lease_id}/evidence/", files={"file": ("photo.png", PNG, "image/png")}, data={"kind": "move_in"})
    assert not item["anchor_tx_hash"], "a wallet party's photo must not be anchored by the server"
    anchor_hash = wallet_submit(wallet, api.get(f"/api/evidence/{item['id']}/anchor-tx/")["tx"])
    item = api.post(f"/api/evidence/{item['id']}/anchor/", {"mode": "wallet", "tx_hash": anchor_hash})
    assert item["anchor_tx_hash"] == anchor_hash
    step(f"wallet-signed evidence anchor verified: {item['explorer_url']}")

    finish_hash = wallet_submit(wallet, api.get(f"/api/leases/{lease_id}/release-tx/")["tx"])
    state = api.post(f"/api/leases/{lease_id}/release/", {"reason": "early_move_out", "mode": "wallet", "tx_hash": finish_hash})
    assert state["status"] == "IN_SETTLEMENT", state["status"]
    proposal = state["proposals"][0]
    assert proposal["signatures_count"] == 0 and not any(a.startswith("sign:") for a in state["actions"])
    step("wallet-signed EscrowFinish verified; the wallet's proposal waits for two demo-key approvals")

    for role in ("landlord", "arbiter"):
        api.post("/api/demo/persona/", {"party_id": personas[role]["party_id"]})
        result = api.post(f"/api/proposals/{proposal['id']}/sign/")
    assert result["lease"]["status"] == "SETTLED", result["lease"]["status"]
    step("landlord and arbiter approved: 250 EUR paid back to the wallet")
    print(f"PASS  {args.base}/leases/{lease_id}/   public check: {args.base}/verify/{lease_id}/")


if __name__ == "__main__":
    main()
