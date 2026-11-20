# safeDeposit — project context (read before every task)

Adapted from "Cauzione — Fable build prompts (Django + JS)". Decisions taken on 2026-11-20 that differ from that doc:
- The app is called **safeDeposit** (the doc's working name was Cauzione). Tagline: "Your deposit, returned by the ledger."
- **No Bootstrap, no CDNs.** The UI uses the existing "whitepaper" design in `static/css/global.css` and `templates/` (see README.md for its building blocks and branding rules). Third-party browser code (xrpl.js, @gemwallet/api) is self-hosted in `static/vendor/` so a strict CSP holds and no visitor IP reaches a third party.
- Browser wallets: GemWallet only (no Crossmark). Demo mode stays the default.
- Django is pinned to 5.2.x.

## What we're building
A web app that protects rental security deposits with native XRP Ledger features. Built in 24 hours for the IXH26 hackathon (Track 2, XRPL applications). It must be demo-ready, not production-ready. Pitch line: "The ledger returns your deposit, not us."

## The mechanism
1. A lease names a tenant, a landlord and an independent arbiter.
2. For each lease the backend creates a fresh settlement account on the XRPL: fund it with XRP, add a trust line to the deposit token, set a SignerList (tenant, landlord, arbiter; weight 1 each; quorum 2), then disable its master key. From then on, money leaves it only with 2 of the 3 signatures.
3. The tenant creates a token escrow (XLS-85) for the deposit: Account = tenant, Destination = settlement account, Amount = deposit in an issued token (our test EUR or USD), Condition = PREIMAGE-SHA-256 crypto-condition, CancelAfter = lease end + grace period. No FinishAfter.
4. Outcomes:
   - Normal end: after CancelAfter our bot submits EscrowCancel (any account may) and the deposit returns to the tenant automatically.
   - Early move-out or damage claim: before CancelAfter, the tenant or landlord submits EscrowFinish with the fulfillment; the deposit moves to the settlement account.
   - Settlement: any 2 of tenant, landlord and arbiter sign payouts from the settlement account (e.g. 100% to the tenant, or 850 / 150).

## Stack (don't change without asking)
- Python 3.11+, Django 5.2, SQLite, server-rendered Django templates.
- Frontend: vanilla JS ES modules in static/js/, no build step, no frameworks, no inline scripts or styles (CSP-safe). Existing whitepaper CSS. xrpl.js browser build self-hosted in static/vendor/, only for client-side ledger reads and wallet transactions.
- XRPL on the server: xrpl-py (5.2+) with the synchronous JsonRpcClient.
- Background work: threading.Thread(daemon=True) plus a polling UI, and a run_bot management command. No Celery, no Redis.
- Config in .env via python-dotenv. Never commit .env.

## Layout
config/ (settings, urls) | core/ (models, lifecycle.py, api.py, views.py, management/commands/) | ledger/ (client.py, services.py, conditions.py, errors.py) | templates/ (base.html, partials/, escrow/, legal/) | static/js/ (app.js, fx.js, sound.js, wallet.js, api.js, ui.js, poll.js, pages/) | static/css/global.css | static/vendor/ | scripts/ | media/

## Network
- XRPL Testnet only: JSON-RPC https://s.altnet.rippletest.net:51234/ | WebSocket wss://s.altnet.rippletest.net:51233 | explorer https://testnet.xrpl.org (/transactions/<hash>, /accounts/<address>).
- Fallback when a feature is disabled on Testnet (temDISABLED): Devnet, JSON-RPC https://s.devnet.rippletest.net:51234/, WebSocket wss://s.devnet.rippletest.net:51233, explorer https://devnet.xrpl.org. Chosen by XRPL_NETWORK=testnet|devnet in .env.
- Demo seeds may be stored in the DB because this is Testnet. The app refuses to start if any URL points at mainnet (ledger/client.py, allow-list of test hosts).
- Checked 2026-11-20 on Testnet: TokenEscrow and Credentials amendments are enabled; Batch is not.

## Data model
- PlatformWallet: role (treasury | bot | issuer_eur | issuer_usd | credential_issuer), address, seed.
- Party: display_name, role (tenant | landlord | arbiter), address, seed (null for external wallets), wallet_type (demo | gemwallet | crossmark).
- Currency: code (EUR | USD), label ('EUR (test)'), issuer_address.
- Lease: uuid id, tenant / landlord / arbiter (FK Party), currency, amount (Decimal), end_at, grace_minutes, cancel_after, settlement_address, settlement_seed (testnet only), condition_hex, fulfillment_hex (SECRET), escrow_owner, escrow_sequence, status, setup_step, release_reason, error, timestamps.
- Lease.status: DRAFT -> SETTLEMENT_READY -> ACTIVE -> RETURNED, or ACTIVE -> IN_SETTLEMENT -> SETTLED; FAILED on errors.
- SettlementProposal: lease, tenant_amount, landlord_amount, reason, proposed_by, status (OPEN | EXECUTED | SUPERSEDED | EXPIRED), prepared_txs (JSON list), signatures (JSON {party_id: [signed tx JSON per prepared tx]}), executed_hashes.
- Evidence: lease, kind (move_in | move_out | damage), file, sha256, uploaded_by, anchor_tx_hash.
- LedgerEvent: lease, kind, tx_hash, summary, created_at. Every on-ledger action writes one; it powers the timeline.

## API contract (JSON; every POST sends Django's CSRF token in X-CSRFToken)
- GET  /api/me/ -> current persona {party_id, role, address, balances}
- POST /api/demo/persona/ {party_id} -> switch the demo persona stored in the session
- POST /api/demo/fund/ -> top up the current persona with XRP and 5,000 test tokens
- POST /api/demo/run-bot/ -> run one bot pass now
- GET  /api/leases/ -> leases where the current persona is a party (?status= filter)
- POST /api/leases/ {landlord_id, arbiter_id, currency, amount, end_in_minutes or end_date, grace_minutes} -> {lease_id}; settlement setup starts in the background
- GET  /api/leases/<id>/ -> full state: status, setup_step, parties, settlement (read live), escrow, seconds_to_cancel_after, proposals, events, evidence, actions (allowed actions for the current persona)
- GET  /api/leases/<id>/public/ -> addresses and escrow sequence only, no secrets
- GET  /api/leases/<id>/escrow-tx/ -> unsigned EscrowCreate JSON for wallet signing
- POST /api/leases/<id>/escrow/ {mode: 'demo'} or {mode: 'wallet', tx_hash}
- GET  /api/leases/<id>/release-tx/ -> unsigned EscrowFinish JSON incl. Fulfillment (tenant or landlord sessions only)
- POST /api/leases/<id>/release/ {reason: 'early_move_out' | 'damage_claim', mode, tx_hash?}
- POST /api/leases/<id>/proposals/ {tenant_amount, landlord_amount, reason}
- POST /api/proposals/<id>/sign/
- POST /api/leases/<id>/evidence/ (multipart: file, kind)
- POST /api/wallet/link/ {address, role}
- Errors: {error: friendly message, code: engine result or internal code} with HTTP 400 / 409 / 500.
- Action names used in 'actions': lock_deposit, release_early, claim_damage, propose, sign:<proposal_id>, upload_evidence, share_verify.

## XRPL rules and gotchas
- Ripple time = seconds since 2000-01-01 UTC (xrpl.utils.datetime_to_ripple_time). Compare deadlines with the latest validated ledger's close time (services.ledger_close_time), never the local clock. Wait about 10 s past a deadline before submitting.
- Crypto-condition without extra libraries, for a random 32-byte preimage p: fulfillment_hex = 'A0228020' + p.hex().upper(); condition_hex = 'A0258020' + sha256(p).hexdigest().upper() + '810120' (ledger/conditions.py).
- Issuer accounts: set DefaultRipple FIRST, then Allow Trust Line Locking (AccountSetAsfFlag.ASF_ALLOW_TRUSTLINE_LOCKING = 17; ledger flag lsfAllowTrustLineLocking = 0x40000000). Trust lines created before DefaultRipple break holder-to-holder payments.
- Settlement account order: fund -> TrustSet -> SignerListSet (quorum 2) -> AccountSet asfDisableMaster. The trust line must exist before the escrow is finished into it.
- An escrow is identified by (Owner, OfferSequence); OfferSequence = the Sequence of the EscrowCreate transaction (result["tx_json"]["Sequence"] in xrpl-py 5 / API v2). Always store it.
- EscrowFinish works only before CancelAfter, EscrowCancel only after. Any account may submit either; Finish needs the fulfillment. A conditional EscrowFinish costs more: if autofill under-prices it, fee = base_fee x (33 + ceil(fulfillment_bytes / 16)).
- Multisigned transactions: set sequence explicitly and last_ledger_sequence = latest validated + 1000 (signatures can arrive minutes apart), autofill the fee with signers_count=2, sign with sign(tx, wallet, multisign=True), combine with multisign(tx, [signed, ...]), submit. tefMAX_LEDGER means expired: rebuild and re-sign.
- Two transactions from the same account must not be built at the same time (same Sequence). services.submit() takes a per-address lock; always send through it.
- Never log or render fulfillment_hex outside the tenant / landlord release flow. Never show seeds anywhere (the admin hides them).
- Translate engine results into friendly errors (ledger/errors.py): tecNO_PERMISSION, tecNO_LINE, tecUNFUNDED_PAYMENT, tecNO_DST, tecCRYPTOCONDITION_ERROR, tefBAD_QUORUM, tefMAX_LEDGER, terPRE_SEQ, temDISABLED.

## Working rules
- Do only the patch you were given; don't start the next one.
- Check APIs in the installed library versions (source, docstrings, README) instead of guessing; xrpl-py and wallet SDKs change.
- Ledger code lives in ledger/services.py as plain functions with no request objects; views and lifecycle code call them.
- Every on-ledger action creates a LedgerEvent with its tx hash.
- When finished: run the acceptance checks, show the output, list changed files and known issues, then stop.

## Progress
- Patch 1 (skeleton and XRPL bootstrap): done 2026-11-20. bootstrap_demo and preflight pass on Testnet.
