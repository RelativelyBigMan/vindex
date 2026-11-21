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
        qr = ROOT / f"spike-{signer}.html"
        qr.write_text(f'<!doctype html><title>Approve as {signer}</title><p>Scan with Xaman as {signer}</p>'
                      f'<img src="{xaman.qr_data_uri(payload["deeplink"])}" width="300">', encoding="utf-8")
        print(f"\nApprove as {signer}: open {payload['deeplink']} on the phone, or scan the QR in {qr}", flush=True)
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
