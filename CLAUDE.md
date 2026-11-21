# safeDeposit — project context (read before every task)

Adapted from "Cauzione — Fable build prompts (Django + JS)". Decisions taken on 2026-11-20 that differ from that doc:
- The app is called **safeDeposit** (the doc's working name was Cauzione). Tagline: "Your deposit, returned by the ledger."
- **No Bootstrap, no CDNs.** The UI uses the existing "whitepaper" design in `static/css/global.css` and `templates/` (see README.md for its building blocks and branding rules). Third-party browser code (xrpl.js) is self-hosted in `static/vendor/` so a strict CSP holds and no visitor IP reaches a third party.
- Decided 2026-11-21 (branch real-accounts-testnet): **Xaman only**. No demo mode, no demo personas, no GemWallet, no arbiter credential. Real accounts sign in with Xaman and every action is a Xaman sign request. Still XRPL Testnet only.
- Django is pinned to 5.2.x.

## What we're building
A web app that protects rental security deposits with native XRP Ledger features. Built in 24 hours for the IXH26 hackathon (Track 2, XRPL applications). It runs on the Testnet with real Xaman accounts; it is not production-ready. Pitch line: "The ledger returns your deposit, not us."

## The mechanism
1. A lease names a tenant, a landlord and an independent arbiter. It starts as an agreement: invite links, everyone accepts, tenant and landlord both approve the arbiter, and only then is the settlement account set up.
2. For each lease the backend creates a fresh settlement account on the XRPL: fund it with XRP, add a trust line to the deposit token, set a SignerList (tenant, landlord, arbiter; weight 1 each; quorum 2), then disable its master key. From then on, money leaves it only with 2 of the 3 signatures.
3. The tenant creates a token escrow (XLS-85) for the deposit: Account = tenant, Destination = settlement account, Amount = deposit in an issued token (our test EUR or USD), Condition = PREIMAGE-SHA-256 crypto-condition, CancelAfter = lease end + grace period. No FinishAfter.
4. Outcomes:
   - Normal end: after CancelAfter our bot submits EscrowCancel (any account may) and the deposit returns to the tenant automatically.
   - Early move-out or damage claim: before CancelAfter, the tenant or landlord submits EscrowFinish with the fulfillment; the deposit moves to the settlement account.
   - Settlement: any 2 of tenant, landlord and arbiter sign payouts from the settlement account (e.g. 100% to the tenant, or 850 / 150).

## Stack (don't change without asking)
- Python 3.11+, Django 5.2, SQLite, server-rendered Django templates.
- Frontend: vanilla JS ES modules in static/js/, no build step, no frameworks, no inline scripts or styles (CSP-safe). Existing whitepaper CSS. xrpl.js browser build self-hosted in static/vendor/, only for client-side ledger reads (public verify page).
- Signing: Xaman REST API through `ledger/xaman.py` (keys XAMAN_API_KEY / XAMAN_API_SECRET in .env); QR codes by `segno`.
- XRPL on the server: xrpl-py (5.2+) with the synchronous JsonRpcClient.
- Background work: threading.Thread(daemon=True) plus a polling UI, and a run_bot management command (the platform bot: returns deposits, closes expired agreements, cancels leases locked after cancellation). No Celery, no Redis.
- Config in .env via python-dotenv. Never commit .env.

## Layout
config/ (settings, urls) | core/ (models, agreements.py, signing.py, faucet.py, lifecycle.py, api.py, views.py, management/commands/) | ledger/ (client.py, services.py, xaman.py, conditions.py, errors.py) | templates/ (base.html, partials/, escrow/, legal/) | static/js/ (app.js, fx.js, sound.js, xaman.js, api.js, ui.js, poll.js, pages/) | static/css/global.css | static/vendor/ | scripts/ | media/

## Network
- XRPL Testnet only: JSON-RPC https://s.altnet.rippletest.net:51234/ | WebSocket wss://s.altnet.rippletest.net:51233 | explorer https://testnet.xrpl.org (/transactions/<hash>, /accounts/<address>).
- Fallback when a feature is disabled on Testnet (temDISABLED): Devnet, JSON-RPC https://s.devnet.rippletest.net:51234/, WebSocket wss://s.devnet.rippletest.net:51233, explorer https://devnet.xrpl.org. Chosen by XRPL_NETWORK=testnet|devnet in .env.
- Only platform wallet seeds (and Testnet settlement seeds, master key disabled) are stored in the DB because this is Testnet; user accounts never give us a key. The app refuses to start if any URL points at mainnet (ledger/client.py, allow-list of test hosts).
- Checked 2026-11-20 on Testnet: TokenEscrow amendment is enabled; Batch is not.

## Data model
- PlatformWallet: role (treasury | bot | issuer_eur | issuer_usd), address, seed.
- Party: an account. address (unique), display_name, xaman_user_token (for push), created_at. A party's role (tenant | landlord | arbiter) comes from each lease.
- Currency: code (EUR | USD), label ('EUR (test)'), issuer_address.
- Lease: uuid id, tenant / landlord / arbiter (FK Party, null until accepted), created_by, arbiter_accepted, tenant_approved_arbiter, landlord_approved_arbiter, currency, amount (Decimal), title, end_at, grace_minutes, cancel_after, settlement_address, settlement_seed (testnet only), condition_hex, fulfillment_hex (SECRET), escrow_owner, escrow_sequence, status, setup_step, release_reason, error, timestamps.
- Lease.status: PENDING_ACCEPTANCE -> DRAFT (setup running) -> SETTLEMENT_READY -> ACTIVE -> RETURNED, or ACTIVE -> IN_SETTLEMENT -> SETTLED; FAILED on errors; CANCELLED before locking.
- Invite: lease, role, token (url-safe), accepted_by, accepted_at, declined_at, revoked. One link per missing party.
- SettlementProposal: lease, tenant_amount, landlord_amount, reason, proposed_by, status (OPEN | EXECUTED | SUPERSEDED | EXPIRED), prepared_txs (JSON list), signatures (JSON {party_id: [signed blob per prepared tx]}), executed_hashes.
- XamanRequest: our uuid, xaman_uuid, purpose (signin | escrow_create | payout_sign | evidence_anchor | trustset), party, lease, proposal, evidence, payload_index, currency_code, deeplink, pushed, status (open | signed | finishing | done | rejected | expired | failed), error, result. The browser only sees our id, the QR and the deep link.
- FaucetGrant: party, kind (activate | tokens:EUR | tokens:USD), created_at; once per account per 24 hours.
- Evidence: lease, kind (move_in | move_out | damage), file, sha256, uploaded_by, anchor_tx_hash.
- LedgerEvent: lease, kind, tx_hash, summary, created_at. Every on-ledger action writes one; it powers the timeline.

## API contract (JSON; every POST sends Django's CSRF token in X-CSRFToken)
- GET/POST /api/me/ -> signed-in account {address, display_name, balances}; POST sets the display name
- POST /api/auth/signin/ -> starts a Xaman SignIn request; POST /api/auth/signout/
- GET  /api/xaman/requests/<id>/ -> poll a sign request (status, QR, deeplink); POST .../cancel/
- GET  /api/faucet/ -> what the account may still claim; POST /api/faucet/activate/, /api/faucet/trustline/ (Xaman request), /api/faucet/tokens/
- GET  /api/invites/<token>/ -> what the invite is for; POST .../accept/, .../decline/
- GET/POST /api/leases/ -> list the account's agreements (?status=); create a draft {role, currency, amount, end_in_minutes or end_date, grace_minutes, title, arbiter_address} -> {lease_id}
- GET  /api/leases/<id>/ -> full state incl. agreement (invites, approvals), settlement, escrow, proposals, events, evidence, actions
- GET  /api/leases/<id>/public/ -> addresses and escrow sequence only, no secrets; GET .../settlement/ -> raw ledger data
- POST /api/leases/<id>/cancel/, /arbiter/ {address} (change the arbiter), /approve-arbiter/
- POST /api/leases/<id>/escrow/ -> Xaman request to lock the deposit (EscrowCreate)
- POST /api/leases/<id>/release/ {reason: 'early_move_out' | 'damage_claim'} -> the platform bot submits the EscrowFinish
- POST /api/leases/<id>/proposals/ {tenant_amount, landlord_amount, reason}
- POST /api/proposals/<id>/sign/ -> Xaman multisign request; POST /api/proposals/<id>/retry/ -> submit approved payouts again
- POST /api/leases/<id>/evidence/ (multipart: file, kind); GET /api/evidence/<id>/file/ (parties only); POST /api/evidence/<id>/anchor/ -> Xaman request
- Errors: {error: friendly message, code: engine result or internal code} with HTTP 400 / 401 / 409 / 500.
- Action names used in 'actions': lock_deposit, release_early, claim_damage, propose, sign:<proposal_id>, upload_evidence, share_verify (plus agreement actions: accept, approve_arbiter, cancel).

## XRPL rules and gotchas
- Ripple time = seconds since 2000-01-01 UTC (xrpl.utils.datetime_to_ripple_time). Compare deadlines with the latest validated ledger's close time (services.ledger_close_time), never the local clock. Wait about 10 s past a deadline before submitting.
- Crypto-condition without extra libraries, for a random 32-byte preimage p: fulfillment_hex = 'A0228020' + p.hex().upper(); condition_hex = 'A0258020' + sha256(p).hexdigest().upper() + '810120' (ledger/conditions.py).
- Issuer accounts: set DefaultRipple FIRST, then Allow Trust Line Locking (AccountSetAsfFlag.ASF_ALLOW_TRUSTLINE_LOCKING = 17; ledger flag lsfAllowTrustLineLocking = 0x40000000). Trust lines created before DefaultRipple break holder-to-holder payments.
- Settlement account order: fund -> TrustSet -> SignerListSet (quorum 2) -> AccountSet asfDisableMaster. The trust line must exist before the escrow is finished into it.
- An escrow is identified by (Owner, OfferSequence); OfferSequence = the Sequence of the EscrowCreate transaction (result["tx_json"]["Sequence"] in xrpl-py 5 / API v2). Always store it.
- EscrowFinish works only before CancelAfter, EscrowCancel only after. Any account may submit either; Finish needs the fulfillment. A conditional EscrowFinish costs more: if autofill under-prices it, fee = base_fee x (33 + ceil(fulfillment_bytes / 16)).
- Releases are submitted by the bot account (any account may submit EscrowFinish), so the fulfillment never leaves the server and is never sent to a browser. EscrowCancel for expired or cancelled-then-locked leases is also the bot's.
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
- Patch 2 (ledger mechanics and smoke test): done 2026-11-20. `manage.py smoke_test --scenario all` passes A, B and C on Testnet. Notes for later patches:
  - `create_settlement_account(..., on_step)` calls `on_step(step, tx_hash)` (two arguments), so each setup step can be stored as a LedgerEvent with its hash.
  - Prepared transactions and signatures are plain XRPL JSON dicts (`prepare_payout_txs`, `sign_prepared`, `execute_multisigned`); they can go straight into the JSON fields.
  - `finish_escrow` raises LedgerError `escrow_expired`, `cancel_escrow` raises `escrow_not_expired`, `prepare_payout_txs` raises `payout_sum_mismatch` / `nothing_to_pay`; `execute_multisigned` raises `tefBAD_QUORUM`, `signature_mismatch` or `ExpiredPreparedTx`.
  - `services.submit()` no longer uses xrpl-py's submit_and_wait: it fails at once on tem / tef / tel results instead of polling until LastLedgerSequence.
  - `build_escrow_finish` returns the fulfillment inside the tx JSON: tenant / landlord only, never log it.
  - Memos are public and permanent: anchor hashes and ids only, never names or other personal data.
- Patch 3 (lifecycle, API, bot): done 2026-11-20.
- Patches 4-7 (UI, wallet + verify, evidence + credential, demo stage + README): done 2026-11-20, in one session. Notes:
  - Pages are shells (core/views.py); static/js/pages/*.js render from the API. Shared pieces: ui.js (el, toast, busy, fill), parts.js, actions.js, splitform.js. Never innerHTML; never pass null to replaceChildren (use ui.fill).
  - runserver runs with --noreload in .claude/launch.json: restart it after Python changes. Browsers cache ES modules: hard-reload after JS changes.
  - CSP is set in core/middleware.py; a new external host needs an entry there (currently only the network's WebSocket).
    - Lease has title (off-chain label) and scenario (A/B/C, set by demo_scenarios; the stage view reads the newest of each).
  - Countdowns keep the earliest computed deadline: slow poll answers must never move it later.
  - Not done: Batch payouts (amendment off on Testnet), Crossmark, a click-through with the real GemWallet extension.
- Real accounts with Xaman (branch real-accounts-testnet): done 2026-11-21. Spec and plan in docs/superpowers/. Demo personas, GemWallet and the arbiter credential are removed; manage.py bootstrap (was bootstrap_demo) creates platform wallets only; preflight uses throw-away wallets.
